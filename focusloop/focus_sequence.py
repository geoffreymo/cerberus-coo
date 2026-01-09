"""
Focus loop orchestration - coordinates camera, telescope, and analysis.

This module provides the FocusLoop class that automates the focus
optimization process by stepping through focus positions, taking
exposures, measuring FWHM, and fitting a parabola to find best focus.
"""

import os
import time
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
from enum import Enum

from .focus_analyzer import FocusAnalyzer, FocusResult


class FocusLoopState(Enum):
    """Focus loop state machine states."""
    IDLE = "idle"
    RUNNING = "running"
    ANALYZING = "analyzing"
    COMPLETE = "complete"
    ERROR = "error"
    ABORTED = "aborted"


@dataclass
class FocusLoopConfig:
    """Configuration for focus loop."""
    # Focus range (telescope focus in mm)
    start_position: float = 30.0
    end_position: float = 45.0
    step_size: float = 2.5

    # Camera settings
    exposure_time: float = 5.0  # seconds (default/fallback)
    filter_exposures: Dict[str, float] = field(default_factory=dict)  # Per-filter exposures

    # Output
    output_dir: str = "/tmp/cerberus_focus"
    object_name: str = "focus"

    # Behavior
    settle_time: float = 2.0  # seconds after focus move
    auto_apply_best: bool = True  # Apply best focus when done

    # Quality checks
    max_fwhm_arcsec: float = 5.0  # Max reasonable FWHM

    # Filters (optional - leave empty for single run)
    filters: List[str] = field(default_factory=list)

    def validate(self):
        """Validate configuration before running."""
        if self.start_position < 1.0 or self.end_position > 74.0:
            raise ValueError("Focus range must be within 1.0-74.0 mm (telescope limits)")
        if self.step_size <= 0:
            raise ValueError("Step size must be positive")
        if self.start_position >= self.end_position:
            raise ValueError("Start position must be less than end position")
        if self.exposure_time <= 0:
            raise ValueError("Exposure time must be positive")

    @property
    def num_positions(self) -> int:
        """Calculate number of focus positions."""
        return int((self.end_position - self.start_position) / self.step_size) + 1


@dataclass
class FocusLoopProgress:
    """Progress information for callbacks."""
    state: FocusLoopState
    current_position: float
    total_positions: int
    completed_positions: int
    current_filter: Optional[str]
    message: str


class FocusLoop:
    """
    Automated focus loop controller.

    Coordinates camera exposures, telescope focus movement, and FWHM
    analysis to find optimal focus position.

    Usage:
        from haletcs import TCSClient
        from cerberus_coo.camera import CerberusCamera
        from cerberus_coo.focusloop import FocusLoop, FocusLoopConfig

        # Basic usage
        with TCSClient() as tcs, CerberusCamera() as cam:
            config = FocusLoopConfig(
                start_position=30.0,
                end_position=45.0,
                step_size=2.5,
                exposure_time=5.0
            )
            loop = FocusLoop(camera=cam, telescope=tcs, config=config)
            result = loop.run()
            print(f"Best focus: {result.best_focus:.2f} mm")

        # With filter wheel
        from cerberus_coo.filterwheel import FilterWheel

        with TCSClient() as tcs, CerberusCamera() as cam:
            fw = FilterWheel(config_path="filters.json")
            config = FocusLoopConfig(
                start_position=30.0,
                end_position=45.0,
                step_size=2.5,
                exposure_time=5.0,
                filters=['r', 'i', 'z']
            )
            loop = FocusLoop(camera=cam, telescope=tcs, filterwheel=fw, config=config)
            results = loop.run()  # Dict[filter_name, FocusResult]

        # With progress callback
        def on_progress(p: FocusLoopProgress):
            print(f"[{p.state.value}] {p.message}")

        loop = FocusLoop(camera=cam, telescope=tcs, config=config)
        loop.on_progress = on_progress
        result = loop.run()
    """

    def __init__(
        self,
        camera,  # CerberusCamera instance
        telescope,  # TCSClient instance
        filterwheel=None,  # Optional FilterWheel instance
        config: FocusLoopConfig = None,
        analyzer: FocusAnalyzer = None,
        api=None  # Optional CerberusAPI for unified imaging
    ):
        """
        Initialize FocusLoop.

        Args:
            camera: CerberusCamera instance (must be connected)
            telescope: TCSClient instance (must be connected)
            filterwheel: Optional FilterWheel instance
            config: FocusLoopConfig (uses defaults if None)
            analyzer: FocusAnalyzer instance (creates default if None)
            api: Optional CerberusAPI instance for unified FITS capture
        """
        self.camera = camera
        self.telescope = telescope
        self.filterwheel = filterwheel
        self.config = config or FocusLoopConfig()
        self.analyzer = analyzer or FocusAnalyzer()
        self.api = api

        self.logger = logging.getLogger(__name__)
        self.state = FocusLoopState.IDLE
        self._abort_requested = False

        # Progress callback
        self.on_progress: Optional[Callable[[FocusLoopProgress], None]] = None

        # Results storage
        self._results: Dict[Optional[str], FocusResult] = {}

    def _report_progress(self, message: str, position: float = 0.0,
                         completed: int = 0, total: int = 0,
                         filter_name: Optional[str] = None):
        """Report progress via callback."""
        self.logger.info(message)
        if self.on_progress:
            progress = FocusLoopProgress(
                state=self.state,
                current_position=position,
                total_positions=total,
                completed_positions=completed,
                current_filter=filter_name,
                message=message
            )
            self.on_progress(progress)

    def _generate_positions(self) -> List[float]:
        """Generate list of focus positions to sample."""
        positions = []
        pos = self.config.start_position
        while pos <= self.config.end_position + 0.001:  # Small epsilon for float comparison
            positions.append(round(pos, 2))
            pos += self.config.step_size
        return positions

    def _take_focus_image(self, position: float,
                          filter_name: Optional[str] = None) -> str:
        """
        Move to focus position and capture image.

        Args:
            position: Focus position in mm
            filter_name: Current filter name (for filename)

        Returns:
            Path to saved FITS file
        """
        # Move focus and verify TCS accepted the command
        self.logger.info(f"Moving focus to {position:.2f} mm")
        result = self.telescope.set_focus(position)
        time.sleep(1)
        if result is False:  # Explicitly check for False (not None)
            raise RuntimeError(f"TCS rejected focus command to {position:.2f} mm")

        # Wait for focus to reach target position
        if hasattr(self.telescope, 'wait_for_focus'):
            # Real telescope: poll until focus reaches target
            if not self.telescope.wait_for_focus(position, tolerance_mm=1.0, timeout_sec=60.0):
                raise RuntimeError(f"Focus move timed out waiting to reach {position:.2f} mm")
        else:
            # Mock telescope or legacy: use fixed settle time
            time.sleep(self.config.settle_time)

        # Additional settle time for mechanical vibration
        time.sleep(self.config.settle_time)

        # Set exposure - use filter-specific exposure if available
        exposure = self.config.filter_exposures.get(filter_name, self.config.exposure_time)
        self.logger.info(f"Capturing {exposure}s exposure")
        self.camera.set_exposure(exposure)

        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if filter_name:
            filename = f"{timestamp}_focus_{position:.2f}_{filter_name}.fits"
        else:
            filename = f"{timestamp}_focus_{position:.2f}.fits"

        filepath = os.path.join(self.config.output_dir, filename)

        # Use API's capture_single_to_fits if available (same controller as regular imaging)
        # TELFOCUS header is automatically added by the API from telescope state
        if hasattr(self, 'api') and self.api is not None:
            result = self.api.capture_single_to_fits(
                filepath=filepath,
                object_name=self.config.object_name
            )
            if result is None:
                raise RuntimeError("Failed to capture focus image via API")
            else:
                self.logger.info(f"Focus image captured at {filepath}")
        else:
            # Fallback: direct camera capture (legacy path)
            frame = self.camera.capture_single()
            if frame is None:
                raise RuntimeError("Failed to capture focus image")

            # Save with metadata (include TELFOCUS since API isn't available)
            header = {
                'TELFOCUS': (position, 'Telescope focus (mm)'),
                'OBJECT': (self.config.object_name, 'Object name'),
            }
            if filter_name:
                header['FILTER'] = (filter_name, 'Filter name')

            self.camera.save_fits(frame, filepath, header_extra=header)

        return filepath

    def _run_focus_sequence(self, filter_name: Optional[str] = None) -> FocusResult:
        """
        Run focus sequence for one filter.

        Args:
            filter_name: Current filter (None if no filterwheel)

        Returns:
            FocusResult
        """
        positions = self._generate_positions()
        images: Dict[float, str] = {}

        for i, position in enumerate(positions):
            if self._abort_requested:
                self.state = FocusLoopState.ABORTED
                return FocusResult(
                    best_focus=0.0,
                    best_fwhm_arcsec=0.0,
                    measurements={},
                    fit_coefficients=(0.0, 0.0, 0.0),
                    success=False,
                    error_message="Aborted by user"
                )

            self._report_progress(
                f"Capturing at focus {position:.2f} mm ({i+1}/{len(positions)})",
                position=position,
                completed=i,
                total=len(positions),
                filter_name=filter_name
            )

            try:
                filepath = self._take_focus_image(position, filter_name)
                images[position] = filepath
            except Exception as e:
                self.logger.error(f"Failed at position {position}: {e}")
                # Continue with remaining positions
                continue

        # Analyze
        self.state = FocusLoopState.ANALYZING
        self._report_progress(
            f"Analyzing {len(images)} focus images",
            filter_name=filter_name
        )

        result = self.analyzer.analyze_focus_sequence(images)

        if result.success:
            self.logger.info(
                f"Best focus: {result.best_focus:.2f} mm, "
                f"FWHM: {result.best_fwhm_arcsec:.3f} arcsec"
            )

            # Optionally save plot
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                plot_name = f"{timestamp}_focus_curve_{filter_name or 'all'}.png"
                plot_path = os.path.join(self.config.output_dir, plot_name)
                self.analyzer.plot_focus_curve(result, output_path=plot_path)
            except Exception as e:
                self.logger.warning(f"Could not save focus plot: {e}")
        else:
            self.logger.error(f"Focus analysis failed: {result.error_message}")

        return result

    def run(self) -> Dict[Optional[str], FocusResult]:
        """
        Run the complete focus loop.

        Returns:
            Dict mapping filter_name (or None for no filter) -> FocusResult
        """
        # Validate config
        self.config.validate()

        self.state = FocusLoopState.RUNNING
        self._abort_requested = False
        self._results = {}

        # Ensure output directory exists
        os.makedirs(self.config.output_dir, exist_ok=True)

        # Get initial focus for reference
        try:
            initial_status = self.telescope.get_status()
            initial_focus = initial_status.focus_mm
            self.logger.info(f"Initial focus position: {initial_focus:.2f} mm")
        except Exception as e:
            self.logger.warning(f"Could not get initial focus: {e}")
            initial_focus = None

        if self.config.filters and self.filterwheel:
            # Run for each filter
            for filter_name in self.config.filters:
                if self._abort_requested:
                    break

                self._report_progress(f"Switching to filter: {filter_name}")
                self.logger.info(f"Running focus loop for filter: {filter_name}")

                try:
                    self.filterwheel.goto(filter_name)  # Already waits internally
                except Exception as e:
                    self.logger.error(f"Filter wheel error: {e}")
                    self._results[filter_name] = FocusResult(
                        best_focus=0.0,
                        best_fwhm_arcsec=0.0,
                        measurements={},
                        fit_coefficients=(0.0, 0.0, 0.0),
                        success=False,
                        error_message=f"Filter wheel error: {e}"
                    )
                    continue

                result = self._run_focus_sequence(filter_name)
                self._results[filter_name] = result

                # Apply best focus for this filter if successful
                if result.success and self.config.auto_apply_best:
                    try:
                        focus_result = self.telescope.set_focus(result.best_focus)
                        if focus_result is False:
                            self.logger.error(f"TCS rejected best focus command: {result.best_focus:.2f} mm")
                        else:
                            self.logger.info(f"Applied best focus: {result.best_focus:.2f} mm")
                    except Exception as e:
                        self.logger.error(f"Failed to apply best focus: {e}")
        else:
            # Single filter or no filterwheel
            result = self._run_focus_sequence()
            self._results[None] = result

            # Apply best focus
            if result.success and self.config.auto_apply_best:
                try:
                    focus_result = self.telescope.set_focus(result.best_focus)
                    if focus_result is False:
                        self.logger.error(f"TCS rejected best focus command: {result.best_focus:.2f} mm")
                    else:
                        self.logger.info(f"Applied best focus: {result.best_focus:.2f} mm")
                except Exception as e:
                    self.logger.error(f"Failed to apply best focus: {e}")

        self.state = FocusLoopState.COMPLETE
        self._report_progress("Focus loop complete")

        return self._results

    def run_single(self) -> FocusResult:
        """
        Convenience method for single-filter focus run.

        Returns:
            FocusResult for the run
        """
        results = self.run()
        # Return the single result (either None key or first filter)
        if None in results:
            return results[None]
        elif results:
            return list(results.values())[0]
        else:
            return FocusResult(
                best_focus=0.0,
                best_fwhm_arcsec=0.0,
                measurements={},
                fit_coefficients=(0.0, 0.0, 0.0),
                success=False,
                error_message="No results"
            )

    def abort(self):
        """Request abort of running focus loop."""
        self._abort_requested = True
        self.logger.warning("Focus loop abort requested")

    @property
    def is_running(self) -> bool:
        """Check if focus loop is currently running."""
        return self.state == FocusLoopState.RUNNING

    @property
    def results(self) -> Dict[Optional[str], FocusResult]:
        """Get results from last run."""
        return self._results


def run_focus_loop(
    telescope,
    camera,
    start: float = 30.0,
    end: float = 45.0,
    step: float = 2.5,
    exposure: float = 5.0,
    output_dir: str = "/tmp/cerberus_focus",
    filterwheel=None,
    filters: List[str] = None
) -> Dict[Optional[str], FocusResult]:
    """
    Convenience function to run a focus loop.

    Args:
        telescope: Connected TCSClient instance
        camera: Connected CerberusCamera instance
        start: Start focus position in mm
        end: End focus position in mm
        step: Step size in mm
        exposure: Exposure time in seconds
        output_dir: Directory for output files
        filterwheel: Optional FilterWheel instance
        filters: Optional list of filter names

    Returns:
        Dict of filter_name -> FocusResult

    Example:
        from haletcs import TCSClient
        from cerberus_coo.camera import CerberusCamera
        from cerberus_coo.focusloop import run_focus_loop

        with TCSClient() as tcs, CerberusCamera() as cam:
            results = run_focus_loop(tcs, cam, start=30, end=45, step=2.5)
            print(f"Best focus: {results[None].best_focus:.2f} mm")
    """
    config = FocusLoopConfig(
        start_position=start,
        end_position=end,
        step_size=step,
        exposure_time=exposure,
        output_dir=output_dir,
        filters=filters or []
    )

    loop = FocusLoop(
        camera=camera,
        telescope=telescope,
        filterwheel=filterwheel,
        config=config
    )

    return loop.run()
