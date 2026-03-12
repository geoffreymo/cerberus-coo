# guiding/engine.py
"""Autoguiding engine - computes drift and issues corrections."""

import time
import logging
from typing import Callable, List, Optional, Tuple

import numpy as np

from config import GuidingConfig

logger = logging.getLogger(__name__)


class GuidingEngine:
    """Autoguiding engine - computes drift and issues corrections.

    Pure logic class with no GUI dependencies. Receives centroid
    measurements, decides when to correct.
    """

    def __init__(self, config: GuidingConfig, plate_scale: float,
                 correction_callback: Callable[[float, float], bool]):
        """
        Args:
            config: GuidingConfig from config.py
            plate_scale: arcsec/pixel
            correction_callback: fn(ra_arcsec, dec_arcsec) -> success
        """
        self._config = config
        self._plate_scale = plate_scale
        self._apply_correction = correction_callback

        # State
        self._enabled = False
        self._calibrating = False
        self._reference: Optional[Tuple[float, float]] = None
        self._position_history: List[Tuple[float, float, float]] = []  # (time, x, y)
        self._last_correction_time = 0.0
        self._last_log_time = 0.0
        self._status = "Not guiding"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def status(self) -> str:
        return self._status

    def start(self):
        """Enter calibration mode."""
        self._enabled = True
        self._calibrating = True
        self._reference = None
        self._position_history = []
        self._status = "Calibrating..."

    def stop(self):
        """Stop guiding."""
        self._enabled = False
        self._calibrating = False
        self._reference = None
        self._status = "Not guiding"

    def reset_reference(self):
        """Re-enter calibration mode."""
        if not self._enabled:
            return
        self._calibrating = True
        self._reference = None
        self._position_history = []
        self._status = "Calibrating..."

    def add_measurement(self, centroid_x: float, centroid_y: float):
        """Feed a new centroid measurement. Called on every FWHM measurement.

        Engine handles all throttling internally.
        """
        if not self._enabled:
            return

        now = time.time()
        self._position_history.append((now, centroid_x, centroid_y))
        self._trim_history()

        if self._calibrating:
            self._update_calibration()
        else:
            self._update_guiding()

    def _update_calibration(self):
        """Check if we have enough samples to set reference."""
        min_samples = self._config.min_samples
        if len(self._position_history) >= min_samples:
            avg_x = np.mean([x for _, x, _ in self._position_history])
            avg_y = np.mean([y for _, _, y in self._position_history])
            self._reference = (float(avg_x), float(avg_y))
            self._calibrating = False
            self._last_correction_time = time.time()
            self._status = "Guiding active"
            logger.info(f"Guiding reference set: ({avg_x:.2f}, {avg_y:.2f}) pixels")
        else:
            have = len(self._position_history)
            self._status = f"Calibrating ({have}/{min_samples})..."

    def _compute_average_position(self) -> Optional[Tuple[float, float]]:
        """Average position over the averaging window, with count-based fallback."""
        window = self._config.averaging_window_seconds
        now = time.time()
        cutoff = now - window

        recent = [(t, x, y) for t, x, y in self._position_history if t >= cutoff]

        # Count-based fallback: if window is too short for min_samples,
        # use the last min_samples entries instead
        min_samples = self._config.min_samples
        if len(recent) < min_samples:
            recent = self._position_history[-min_samples:]
            if len(recent) < min_samples:
                return None

        avg_x = np.mean([x for _, x, _ in recent])
        avg_y = np.mean([y for _, _, y in recent])
        return (float(avg_x), float(avg_y))

    def _update_guiding(self):
        """Compute drift and apply correction if needed."""
        now = time.time()
        current = self._compute_average_position()
        if current is None or self._reference is None:
            return

        drift_x = current[0] - self._reference[0]
        drift_y = current[1] - self._reference[1]

        drift_x_arcsec = drift_x * self._plate_scale
        drift_y_arcsec = drift_y * self._plate_scale
        drift_total = np.sqrt(drift_x_arcsec**2 + drift_y_arcsec**2)

        self._status = f"Drift: {drift_total:.2f}\" ({drift_x_arcsec:+.2f}, {drift_y_arcsec:+.2f})"

        # Time-based correction throttle
        threshold = self._config.correction_threshold_arcsec
        interval = self._config.correction_interval_seconds

        # Periodic log
        if (now - self._last_log_time) >= interval:
            self._last_log_time = now
            if drift_total >= threshold:
                logger.info(f"GUIDING: drift={drift_total:.2f}\" (RA:{drift_x_arcsec:+.2f}\", Dec:{drift_y_arcsec:+.2f}\") - correction needed")
            else:
                logger.info(f"GUIDING: drift={drift_total:.2f}\" (RA:{drift_x_arcsec:+.2f}\", Dec:{drift_y_arcsec:+.2f}\") - within threshold ({threshold:.2f}\")")

        if drift_total >= threshold and (now - self._last_correction_time) >= interval:
            self._apply_guiding_correction(drift_x_arcsec, drift_y_arcsec)

    def _apply_guiding_correction(self, drift_x_arcsec: float, drift_y_arcsec: float):
        """Apply correction via callback, then clear history to let new position settle."""
        config = self._config

        ra_corr = drift_x_arcsec * config.x_to_ra_sign * config.guide_gain
        dec_corr = drift_y_arcsec * config.y_to_dec_sign * config.guide_gain

        max_corr = config.max_correction_arcsec
        ra_corr = np.clip(ra_corr, -max_corr, max_corr)
        dec_corr = np.clip(dec_corr, -max_corr, max_corr)

        logger.info(f"Applying guiding correction: RA={ra_corr:+.3f}\", Dec={dec_corr:+.3f}\"")

        try:
            success = self._apply_correction(float(ra_corr), float(dec_corr))
            if success:
                self._last_correction_time = time.time()
                self._position_history = []  # Reset to let telescope settle
            else:
                logger.warning("Guiding correction failed")
        except Exception as e:
            logger.error(f"Error applying guiding correction: {e}")

    def _trim_history(self):
        """Keep history bounded."""
        max_entries = max(100, self._config.min_samples * 10)
        if len(self._position_history) > max_entries:
            self._position_history = self._position_history[-max_entries:]
