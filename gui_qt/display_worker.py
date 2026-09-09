"""
Per-camera frame processing thread for the live view.

Pulls frames from the API display queue, computes display scaling, statistics,
FWHM tracking (with star following), guiding measurements and aperture
photometry OFF the GUI thread, then hands a ready-to-paint 8-bit image to the
GUI. Only the latest frame is ever processed (older ones are dropped).
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from ..config import get_config
from ..guiding.engine import GuidingEngine
from . import analysis

logger = logging.getLogger(__name__)


@dataclass
class FrameResult:
    """Everything the GUI needs to repaint one frame."""
    image8: np.ndarray
    shape: Tuple[int, int]
    vmin: float
    vmax: float
    fps: float
    mean: float
    max: float
    fwhm_arcsec: Optional[float] = None
    fwhm_ok: bool = True
    fwhm_target: Optional[Tuple[int, int]] = None
    fwhm_box_px: int = 0
    guiding_status: str = ""
    target_flux: Optional[float] = None
    comp_flux: Optional[float] = None
    relative_flux: Optional[float] = None
    frame_index: int = 0


@dataclass
class DisplaySettings:
    vmin: float = 200.0
    vmax: float = 300.0
    auto_scale: bool = False
    fwhm_box_arcsec: float = 4.0
    fwhm_target: Optional[Tuple[int, int]] = None
    photometry_enabled: bool = False
    target_aperture: Optional[Tuple[int, int]] = None
    comparison_aperture: Optional[Tuple[int, int]] = None
    aperture_radius: int = 20
    annulus_inner: int = 30
    annulus_outer: int = 45
    fwhm_every_n: int = 1


class DisplayWorker(QThread):
    """Consumes display frames for one camera and emits FrameResult objects."""

    frame_ready = pyqtSignal(object)      # FrameResult
    guiding_changed = pyqtSignal(str)     # status text
    fwhm_target_moved = pyqtSignal(int, int)

    def __init__(self, api, camera_index: int, parent=None):
        super().__init__(parent)
        self.api = api
        self.camera_index = camera_index
        config = get_config()
        self._plate_scale = config.instrument.plate_scale_arcsec_per_pixel
        self._settings = DisplaySettings(fwhm_box_arcsec=config.instrument.fwhm_box_size_arcsec)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._consumed = threading.Event()
        self._consumed.set()
        self._paused = threading.Event()

        self.last_frame: Optional[np.ndarray] = None
        self._frame_count = 0
        self._frame_index = 0
        self._fps = 0.0
        self._fps_time = time.time()
        self._last_photometry_time = 0.0

        # Histories (guarded by _lock)
        self.fwhm_history: List[Tuple[float, float]] = []
        self.fwhm_history_max = 5000
        self.photometry_data: List[dict] = []
        self.photometry_data_max = 20000

        self.guiding = GuidingEngine(config.guiding, self._plate_scale, self._apply_guiding_correction)

    # ---- settings (GUI thread) ------------------------------------------

    def update_settings(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if not hasattr(self._settings, k):
                    raise AttributeError(k)
                setattr(self._settings, k, v)

    def get_settings(self) -> DisplaySettings:
        with self._lock:
            return DisplaySettings(**vars(self._settings))

    @property
    def plate_scale(self) -> float:
        return self._plate_scale

    def fwhm_box_pixels(self) -> int:
        with self._lock:
            arcsec = self._settings.fwhm_box_arcsec
        px = int(arcsec / self._plate_scale)
        return px if px % 2 == 0 else px + 1

    def set_fwhm_target(self, x: Optional[int], y: Optional[int] = None):
        with self._lock:
            self._settings.fwhm_target = None if x is None else (int(x), int(y))
            self.fwhm_history = []

    def clear_photometry(self):
        with self._lock:
            self.photometry_data = []

    def get_fwhm_history(self) -> List[Tuple[float, float]]:
        with self._lock:
            return list(self.fwhm_history)

    def get_photometry_data(self) -> List[dict]:
        with self._lock:
            return list(self.photometry_data)

    def frame_consumed(self):
        """GUI signals it has painted the last frame; worker may produce another."""
        self._consumed.set()

    def pause(self, paused: bool):
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def stop(self):
        self._stop.set()
        self._consumed.set()
        if self.isRunning():
            self.wait(2000)

    # ---- guiding -----------------------------------------------------------

    def _apply_guiding_correction(self, ra_arcsec: float, dec_arcsec: float) -> bool:
        return self.api.move_offset(ra_arcsec, dec_arcsec)

    def start_guiding(self) -> Tuple[bool, str]:
        with self._lock:
            has_target = self._settings.fwhm_target is not None
        if not has_target:
            return False, "Set FWHM target first!"
        if not self.api.state.telescope_connected:
            return False, "Telescope not connected!"
        self.guiding.start()
        self.guiding_changed.emit(self.guiding.status)
        return True, self.guiding.status

    def stop_guiding(self):
        self.guiding.stop()
        self.guiding_changed.emit(self.guiding.status)

    def reset_guiding(self):
        self.guiding.reset_reference()
        self.guiding_changed.emit(self.guiding.status)

    # ---- main loop ---------------------------------------------------------

    def run(self):
        logger.debug(f"Display worker for camera {self.camera_index} started")
        while not self._stop.is_set():
            if self._paused.is_set():
                # Keep the API display queue drained so it doesn't hold stale frames
                self.api.get_display_frame(camera_index=self.camera_index, timeout=0.1)
                continue
            frame = self.api.get_display_frame(camera_index=self.camera_index, timeout=0.1)
            if frame is None:
                continue
            # Wait until the GUI has consumed the previous result (bounded)
            self._consumed.wait(timeout=0.25)
            self._consumed.clear()
            try:
                result = self._process(frame)
            except Exception as e:
                logger.error(f"Display processing error: {e}")
                self._consumed.set()
                continue
            self.frame_ready.emit(result)
        logger.debug(f"Display worker for camera {self.camera_index} stopped")

    def _process(self, frame: np.ndarray) -> FrameResult:
        self.last_frame = frame
        self._frame_count += 1
        self._frame_index += 1
        now = time.time()
        elapsed = now - self._fps_time
        if elapsed >= 1.0:
            self._fps = self._frame_count / elapsed
            self._frame_count = 0
            self._fps_time = now

        s = self.get_settings()
        mean, vmax_, _, _ = analysis.frame_stats(frame)
        if s.auto_scale:
            vmin, vmax = analysis.auto_scale_limits(frame)
        else:
            vmin, vmax = s.vmin, s.vmax
        image8 = analysis.scale_to_8bit(frame, vmin, vmax)

        result = FrameResult(
            image8=np.ascontiguousarray(image8), shape=frame.shape[:2],
            vmin=vmin, vmax=vmax, fps=self._fps, mean=mean, max=vmax_,
            frame_index=self._frame_index,
        )

        # FWHM tracking
        if s.fwhm_target is not None and (self._frame_index % max(1, s.fwhm_every_n) == 0):
            box = self.fwhm_box_pixels()
            fwhm_px, offset = analysis.measure_fwhm_gaussian(frame, s.fwhm_target, box)
            result.fwhm_box_px = box
            if fwhm_px is not None and offset is not None:
                fwhm_arcsec = fwhm_px * self._plate_scale
                result.fwhm_arcsec = fwhm_arcsec
                with self._lock:
                    self.fwhm_history.append((now, fwhm_arcsec))
                    if len(self.fwhm_history) > self.fwhm_history_max:
                        self.fwhm_history = self.fwhm_history[-self.fwhm_history_max:]
                # Follow the star: recentre the box on the fitted centroid unless the
                # centroid is near the box edge (star leaving / bad fit)
                dx, dy = offset
                edge_margin = (box // 2) * 0.6
                if abs(dx) < edge_margin and abs(dy) < edge_margin:
                    # Absolute centroid in image coordinates. The engine needs absolute
                    # positions (feeding box-relative offsets made guiding inert - G51).
                    abs_x = s.fwhm_target[0] + dx
                    abs_y = s.fwhm_target[1] + dy
                    new_target = (int(round(abs_x)), int(round(abs_y)))
                    if new_target != s.fwhm_target:
                        with self._lock:
                            if self._settings.fwhm_target == s.fwhm_target:
                                self._settings.fwhm_target = new_target
                        self.fwhm_target_moved.emit(*new_target)
                    s.fwhm_target = new_target
                    self.guiding.add_measurement(abs_x, abs_y)
                else:
                    logger.debug(f"Skipping guiding measurement: centroid offset ({dx:.1f}, {dy:.1f}) near box edge")
            else:
                result.fwhm_ok = False
            result.fwhm_target = s.fwhm_target
        elif s.fwhm_target is not None:
            result.fwhm_target = s.fwhm_target
            result.fwhm_box_px = self.fwhm_box_pixels()

        if self.guiding.enabled:
            result.guiding_status = self.guiding.status
        else:
            result.guiding_status = "Not guiding"

        # Photometry (rate limited to ~20 Hz like the Tk GUI)
        if s.photometry_enabled and s.target_aperture is not None and (now - self._last_photometry_time) >= 0.05:
            self._last_photometry_time = now
            t_flux = analysis.compute_flux(frame, *s.target_aperture, s.aperture_radius, s.annulus_inner, s.annulus_outer)
            c_flux = None
            if s.comparison_aperture is not None:
                c_flux = analysis.compute_flux(frame, *s.comparison_aperture, s.aperture_radius, s.annulus_inner, s.annulus_outer)
            rel = (t_flux / c_flux) if (t_flux is not None and c_flux is not None and c_flux > 0) else None
            result.target_flux, result.comp_flux, result.relative_flux = t_flux, c_flux, rel
            with self._lock:
                self.photometry_data.append({'time': now, 'target_flux': t_flux, 'comp_flux': c_flux, 'relative_flux': rel})
                if len(self.photometry_data) > self.photometry_data_max:
                    self.photometry_data = self.photometry_data[-self.photometry_data_max:]

        return result
