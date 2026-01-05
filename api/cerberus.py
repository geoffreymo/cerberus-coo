# api/cerberus.py
"""
Main Cerberus API for controlling the high-speed imaging system.
"""

import logging
import threading
import time
import queue
from typing import Callable, Optional, List, Dict, Any

import numpy as np

from ..hardware.camera import CameraController
from ..hardware.telescope import TelescopeController
from ..acquisition import FITSWriter
from ..config import get_config
from .state import SystemState

logger = logging.getLogger(__name__)

# Optional imports
try:
    from ..filterwheel.filterwheel import FilterWheel
    FILTERWHEEL_AVAILABLE = True
except ImportError:
    FILTERWHEEL_AVAILABLE = False
    FilterWheel = None

try:
    from ..focusloop import FocusLoop, FocusLoopConfig, FocusResult
    FOCUSLOOP_AVAILABLE = True
except ImportError:
    FOCUSLOOP_AVAILABLE = False
    FocusLoop = None
    FocusLoopConfig = None
    FocusResult = None


class CerberusAPI:
    """
    Main interface to the Cerberus high-speed imager system.

    This is the central API that scripts and GUI use to control all
    hardware components (camera, telescope, filter wheel) and manage
    data acquisition.

    Example usage:
        api = CerberusAPI()

        # Connect to hardware
        api.connect_camera()
        api.connect_telescope()

        # Configure camera
        api.set_exposure(0.1)  # 100ms

        # Start streaming and saving
        api.start_streaming()
        api.start_saving("M31", "/data/tonight")

        # Capture for 5 minutes
        time.sleep(300)

        # Stop
        api.stop_saving()
        api.stop_streaming()

        # Disconnect
        api.disconnect_camera()
        api.disconnect_telescope()
    """

    def __init__(self):
        """Initialize the Cerberus API."""
        # Hardware controllers
        self.camera = CameraController()
        self.telescope = TelescopeController()
        self.filterwheel: Optional['FilterWheel'] = None
        self.writer = FITSWriter()

        # System state
        self._state = SystemState()
        self._state_lock = threading.Lock()

        # Callbacks
        self._frame_callbacks: List[Callable] = []
        self._status_callbacks: List[Callable] = []
        self._callback_lock = threading.Lock()

        # Display frame queue (for GUI)
        self._display_queue: queue.Queue = queue.Queue(maxsize=2)

        # Register internal frame handler
        self.camera.on_frame(self._on_camera_frame)

    # ==========================================================================
    # Camera Control
    # ==========================================================================

    def connect_camera(self, camera_index: int = 0) -> bool:
        """
        Connect to the camera.

        Args:
            camera_index: Camera device index

        Returns:
            True if successful
        """
        logger.info("Connecting to camera...")
        success = self.camera.connect(camera_index)

        with self._state_lock:
            self._state.camera_connected = success
            if success:
                self._state.camera_exposure = self.camera.get_exposure()

        self._notify_status_change()
        return success

    def disconnect_camera(self):
        """Disconnect from the camera."""
        logger.info("Disconnecting camera...")

        if self._state.camera_streaming:
            self.stop_streaming()

        self.camera.disconnect()

        with self._state_lock:
            self._state.camera_connected = False
            self._state.camera_streaming = False

        self._notify_status_change()

    def set_exposure(self, seconds: float) -> bool:
        """
        Set camera exposure time.

        Args:
            seconds: Exposure time in seconds

        Returns:
            True if successful
        """
        success = self.camera.set_exposure(seconds)
        if success:
            with self._state_lock:
                self._state.camera_exposure = seconds
            self._notify_status_change()
        return success

    def get_exposure(self) -> Optional[float]:
        """Get current exposure time in seconds."""
        return self.camera.get_exposure()

    def set_binning(self, factor: int) -> bool:
        """
        Set camera binning factor.

        Args:
            factor: Binning factor (1, 2, or 4)

        Returns:
            True if successful
        """
        return self.camera.set_binning(factor)

    def set_trigger_source(self, source: str) -> bool:
        """
        Set camera trigger source.

        Args:
            source: 'internal', 'external', or 'software'

        Returns:
            True if successful
        """
        return self.camera.set_trigger_source(source)

    def set_camera_property(self, name: str, value: float) -> bool:
        """
        Set a camera property.

        Args:
            name: Property name
            value: Property value

        Returns:
            True if successful
        """
        return self.camera.set_property(name, value)

    def get_camera_params(self) -> Dict[str, Any]:
        """Get all camera parameters."""
        return self.camera.get_all_params()

    # ==========================================================================
    # Streaming Control
    # ==========================================================================

    def start_streaming(self, align_to_second: bool = True) -> bool:
        """
        Start camera streaming.

        Args:
            align_to_second: Align capture start to next integer second

        Returns:
            True if successful
        """
        logger.info("Starting streaming...")
        success = self.camera.start_streaming(align_to_second)

        with self._state_lock:
            self._state.camera_streaming = success
            self._state.camera_frames_captured = 0

        self._notify_status_change()
        return success

    def stop_streaming(self) -> bool:
        """Stop camera streaming."""
        logger.info("Stopping streaming...")

        # Stop saving first if active
        if self._state.is_saving:
            self.stop_saving()

        success = self.camera.stop_streaming()

        with self._state_lock:
            self._state.camera_streaming = False

        self._notify_status_change()
        return success

    def is_streaming(self) -> bool:
        """Check if camera is streaming."""
        return self.camera.is_streaming()

    def capture_single(self, timeout_ms: int = 30000) -> Optional[np.ndarray]:
        """
        Capture a single frame.

        Args:
            timeout_ms: Timeout in milliseconds

        Returns:
            Frame data as numpy array, or None if failed
        """
        return self.camera.capture_single(timeout_ms)

    # ==========================================================================
    # Acquisition Control
    # ==========================================================================

    def start_saving(
        self,
        object_name: str,
        output_dir: str,
        frames_per_cube: int = 1000
    ) -> bool:
        """
        Start saving frames to FITS cubes.

        Args:
            object_name: Object name for filenames and headers
            output_dir: Output directory
            frames_per_cube: Number of frames per FITS cube

        Returns:
            True if successful
        """
        if not self._state.camera_streaming:
            logger.error("Cannot save: camera not streaming")
            return False

        logger.info(f"Starting save: {object_name} to {output_dir}")

        # Configure timing info
        timing_info = {
            'time_before_cap_start': self.camera.time_before_cap_start,
            'time_after_cap_start': self.camera.time_after_cap_start,
            'exposure_time': self.camera.get_exposure(),
            'trigger_source': self.camera.get_property('TRIGGER_SOURCE'),
        }

        # Configure and start writer
        self.writer.configure(
            output_dir=output_dir,
            object_name=object_name,
            frames_per_cube=frames_per_cube,
            timing_info=timing_info
        )
        self.writer.set_camera_params(self.camera.get_all_params())

        # Set telescope data if connected
        self._update_writer_telescope_data()

        self.writer.start()

        with self._state_lock:
            self._state.is_saving = True
            self._state.save_object_name = object_name
            self._state.save_output_dir = output_dir
            self._state.frames_saved = 0
            self._state.frames_dropped = 0
            self._state.cubes_saved = 0

        self._notify_status_change()
        return True

    def stop_saving(self):
        """Stop saving frames."""
        logger.info("Stopping save...")
        self.writer.stop()

        with self._state_lock:
            self._state.is_saving = False
            self._state.frames_saved = self.writer.frames_written
            self._state.frames_dropped = self.writer.frames_dropped
            self._state.cubes_saved = self.writer.cubes_written

        self._notify_status_change()

    def is_saving(self) -> bool:
        """Check if currently saving."""
        return self.writer.is_running

    # ==========================================================================
    # Telescope Control
    # ==========================================================================

    def connect_telescope(self, host: str = None, port: int = None) -> bool:
        """
        Connect to the telescope.

        Args:
            host: TCS host address
            port: TCS port

        Returns:
            True if successful
        """
        logger.info("Connecting to telescope...")

        if host or port:
            self.telescope = TelescopeController(host=host, port=port)

        success = self.telescope.connect()

        with self._state_lock:
            self._state.telescope_connected = success
            if success:
                self._state.telescope_focus = self.telescope.get_focus()
                pos = self.telescope.get_position()
                if pos:
                    self._state.telescope_ra = pos.ra
                    self._state.telescope_dec = pos.dec
                    self._state.telescope_ha = pos.ha
                    self._state.telescope_lst = pos.lst
                    self._state.telescope_airmass = pos.airmass
                    self._state.telescope_utc = f"{pos.utc_day} {pos.utc_time}"

        self._notify_status_change()
        return success

    def disconnect_telescope(self):
        """Disconnect from the telescope."""
        logger.info("Disconnecting telescope...")
        self.telescope.disconnect()

        with self._state_lock:
            self._state.telescope_connected = False
            self._state.telescope_focus = None
            self._state.telescope_ra = None
            self._state.telescope_dec = None
            self._state.telescope_ha = None
            self._state.telescope_lst = None
            self._state.telescope_airmass = None
            self._state.telescope_utc = None

        self._notify_status_change()

    def set_focus(self, position_mm: float) -> bool:
        """
        Set telescope focus to absolute position.

        Args:
            position_mm: Focus position in mm (1.0-74.0)

        Returns:
            True if successful
        """
        success = self.telescope.set_focus(position_mm)
        if success:
            with self._state_lock:
                self._state.telescope_focus = position_mm
            self._notify_status_change()
        return success

    def offset_focus(self, offset_mm: float) -> bool:
        """
        Offset telescope focus.

        Args:
            offset_mm: Focus offset in mm

        Returns:
            True if successful
        """
        success = self.telescope.offset_focus(offset_mm)
        if success:
            focus = self.telescope.get_focus()
            with self._state_lock:
                self._state.telescope_focus = focus
            self._notify_status_change()
        return success

    def get_focus(self) -> Optional[float]:
        """Get current focus position in mm."""
        return self.telescope.get_focus()

    def move_offset(self, ra_arcsec: float, dec_arcsec: float) -> bool:
        """
        Move telescope by offset.

        Args:
            ra_arcsec: RA offset in arcseconds
            dec_arcsec: Dec offset in arcseconds

        Returns:
            True if successful
        """
        return self.telescope.move_offset(ra_arcsec, dec_arcsec)

    # ==========================================================================
    # Filter Wheel Control
    # ==========================================================================

    def connect_filterwheel(self, config_path: str = None) -> bool:
        """
        Connect to the filter wheel.

        Uses filter configuration from main config.json by default.
        A separate config_path can be provided for legacy support.

        Args:
            config_path: Path to filter configuration JSON (optional, uses main config if None)

        Returns:
            True if successful
        """
        if not FILTERWHEEL_AVAILABLE:
            logger.error("FilterWheel module not available")
            return False

        try:
            logger.info("Connecting to filter wheel...")
            config = get_config()

            if config_path:
                # Legacy: use separate config file
                self.filterwheel = FilterWheel(
                    library_path=config.filterwheel.library_path,
                    config_path=config_path
                )
            else:
                # Use main config
                self.filterwheel = FilterWheel(
                    library_path=config.filterwheel.library_path,
                    filters=config.filterwheel.filters
                )

            with self._state_lock:
                self._state.filterwheel_connected = True
                self._state.current_filter = self.filterwheel.filter
                self._state.available_filters = list(self.filterwheel.filters.values())

            self._notify_status_change()
            return True

        except Exception as e:
            logger.error(f"Failed to connect filter wheel: {e}")
            self.filterwheel = None
            return False

    def disconnect_filterwheel(self):
        """Disconnect from the filter wheel."""
        if self.filterwheel is not None:
            try:
                self.filterwheel.close()
            except:
                pass
            self.filterwheel = None

        with self._state_lock:
            self._state.filterwheel_connected = False
            self._state.current_filter = None

        self._notify_status_change()

    def set_filter(self, name: str, apply_focus_offset: bool = True) -> bool:
        """
        Set filter by name, optionally applying focus offset.

        When apply_focus_offset is True and the telescope is connected,
        the focus will be adjusted based on the configured offset for
        this filter relative to the previous filter.

        Args:
            name: Filter name
            apply_focus_offset: Apply configured focus offset (default True)

        Returns:
            True if successful
        """
        if self.filterwheel is None:
            logger.error("Filter wheel not connected")
            return False

        try:
            # Get previous filter for offset calculation
            previous_filter = self._state.current_filter

            # Change filter
            self.filterwheel.filter = name
            self.filterwheel.wait_for_move()

            with self._state_lock:
                self._state.current_filter = name

            # Apply focus offset if requested and telescope is connected
            if apply_focus_offset and self._state.telescope_connected and previous_filter:
                self._apply_filter_focus_offset(previous_filter, name)

            self._notify_status_change()
            return True

        except Exception as e:
            logger.error(f"Failed to set filter: {e}")
            return False

    def _apply_filter_focus_offset(self, from_filter: str, to_filter: str):
        """
        Apply focus offset when changing filters.

        The offset is calculated as: new_focus = current_focus + (to_offset - from_offset)

        Args:
            from_filter: Previous filter name
            to_filter: New filter name
        """
        try:
            config = get_config()
            from_offset = config.get_filter_focus_offset(from_filter)
            to_offset = config.get_filter_focus_offset(to_filter)

            delta = to_offset - from_offset

            if abs(delta) > 0.001:  # Only move if significant difference
                logger.info(f"Applying focus offset: {from_filter} -> {to_filter}: {delta:+.3f} mm")
                self.offset_focus(delta)
            else:
                logger.debug(f"No focus offset needed: {from_filter} -> {to_filter}")

        except Exception as e:
            logger.warning(f"Could not apply focus offset: {e}")

    def get_filter(self) -> Optional[str]:
        """Get current filter name."""
        if self.filterwheel is None:
            return None
        return self.filterwheel.filter

    def get_available_filters(self) -> List[str]:
        """Get list of available filter names."""
        if self.filterwheel is None:
            return []
        return list(self.filterwheel.filters.values())

    def get_filter_focus_offset(self, filter_name: str = None) -> float:
        """
        Get focus offset for a filter.

        Args:
            filter_name: Filter name (uses current filter if None)

        Returns:
            Focus offset in mm
        """
        if filter_name is None:
            filter_name = self._state.current_filter
        if filter_name is None:
            return 0.0

        config = get_config()
        return config.get_filter_focus_offset(filter_name)

    def set_filter_focus_offset(self, filter_name: str, offset_mm: float, save: bool = True) -> bool:
        """
        Set focus offset for a filter.

        Args:
            filter_name: Filter name
            offset_mm: Focus offset in mm
            save: Save to config file (default True)

        Returns:
            True if successful
        """
        try:
            config = get_config()
            config.filterwheel.focus_offsets_mm[filter_name] = offset_mm
            logger.info(f"Set focus offset for {filter_name}: {offset_mm:.3f} mm")

            if save:
                self._save_config()

            return True
        except Exception as e:
            logger.error(f"Failed to set focus offset: {e}")
            return False

    def calibrate_filter_focus(self, reference_filter: str = None) -> bool:
        """
        Calibrate focus offsets for all filters.

        Runs a focus loop for each filter and saves the difference from
        a reference filter (defaults to current filter or first filter).

        This is a long-running operation - run focus loop for each filter.

        Args:
            reference_filter: Reference filter name (offset = 0.0)

        Returns:
            True if successful
        """
        if not FOCUSLOOP_AVAILABLE:
            logger.error("FocusLoop module not available")
            return False

        filters = self.get_available_filters()
        if not filters:
            logger.error("No filters available")
            return False

        # Use reference filter or current/first filter
        if reference_filter is None:
            reference_filter = self._state.current_filter or filters[0]

        logger.info(f"Calibrating filter focus with reference: {reference_filter}")

        # Run focus loop for each filter
        best_focus_positions = {}

        for filter_name in filters:
            logger.info(f"Running focus loop for filter: {filter_name}")
            self.set_filter(filter_name, apply_focus_offset=False)

            result = self.run_focus_loop()
            if result and result.success:
                best_focus_positions[filter_name] = result.best_focus
                logger.info(f"  Best focus for {filter_name}: {result.best_focus:.2f} mm")
            else:
                logger.warning(f"  Focus loop failed for {filter_name}")

        # Calculate offsets relative to reference
        if reference_filter not in best_focus_positions:
            logger.error(f"Reference filter {reference_filter} failed")
            return False

        reference_focus = best_focus_positions[reference_filter]
        logger.info(f"Reference focus ({reference_filter}): {reference_focus:.2f} mm")

        for filter_name, best_focus in best_focus_positions.items():
            offset = best_focus - reference_focus
            self.set_filter_focus_offset(filter_name, offset, save=False)
            logger.info(f"  {filter_name}: offset = {offset:+.3f} mm")

        # Save all offsets
        self._save_config()

        # Return to reference filter
        self.set_filter(reference_filter, apply_focus_offset=False)
        self.set_focus(reference_focus)

        return True

    def _save_config(self):
        """Save current config to file."""
        try:
            import json
            from ..config import DEFAULT_CONFIG_PATH

            config = get_config()

            # Build config dict
            config_dict = {
                "telescope": {
                    "host": config.telescope.host,
                    "port": config.telescope.port,
                    "timeout_seconds": config.telescope.timeout_seconds,
                    "focus_min_mm": config.telescope.focus_min_mm,
                    "focus_max_mm": config.telescope.focus_max_mm
                },
                "camera": {
                    "buffer_size": config.camera.buffer_size,
                    "defaults": config.camera.defaults,
                    "capture_timeout_ms": config.camera.capture_timeout_ms,
                    "align_to_second_offset": config.camera.align_to_second_offset
                },
                "filterwheel": {
                    "library_path": config.filterwheel.library_path,
                    "filters": config.filterwheel.filters,
                    "focus_offsets_mm": config.filterwheel.focus_offsets_mm
                },
                "focusloop": {
                    "start_position_mm": config.focusloop.start_position_mm,
                    "end_position_mm": config.focusloop.end_position_mm,
                    "step_size_mm": config.focusloop.step_size_mm,
                    "exposure_time_seconds": config.focusloop.exposure_time_seconds,
                    "settle_time_seconds": config.focusloop.settle_time_seconds,
                    "max_fwhm_arcsec": config.focusloop.max_fwhm_arcsec,
                    "auto_apply_best": config.focusloop.auto_apply_best
                },
                "instrument": {
                    "plate_scale_arcsec_per_pixel": config.instrument.plate_scale_arcsec_per_pixel,
                    "saturation_level_adu": config.instrument.saturation_level_adu,
                    "min_fwhm_pixels": config.instrument.min_fwhm_pixels,
                    "timestamp_rollover_threshold": config.instrument.timestamp_rollover_threshold,
                    "framestamp_rollover_threshold": config.instrument.framestamp_rollover_threshold
                },
                "acquisition": {
                    "max_queue_size": config.acquisition.max_queue_size,
                    "max_pending_writes": config.acquisition.max_pending_writes,
                    "frames_per_cube": config.acquisition.frames_per_cube,
                    "thread_pool_workers": config.acquisition.thread_pool_workers,
                    "backpressure_threshold": config.acquisition.backpressure_threshold
                },
                "paths": {
                    "default_output_dir": config.paths.default_output_dir,
                    "focus_output_dir": config.paths.focus_output_dir
                },
                "gui": {
                    "status_update_interval_ms": config.gui.status_update_interval_ms,
                    "default_object_name": config.gui.default_object_name,
                    "default_focus_display_mm": config.gui.default_focus_display_mm
                }
            }

            with open(DEFAULT_CONFIG_PATH, 'w') as f:
                json.dump(config_dict, f, indent=4)

            logger.info(f"Saved config to: {DEFAULT_CONFIG_PATH}")

        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    # ==========================================================================
    # Focus Loop
    # ==========================================================================

    def run_focus_loop(self, config: 'FocusLoopConfig' = None,
                        on_progress: Callable = None) -> Optional[Dict]:
        """
        Run automated focus loop.

        Supports multi-filter focus runs when filterwheel is connected
        and config.filters is specified.

        Args:
            config: Focus loop configuration
            on_progress: Optional callback for progress updates

        Returns:
            Dict mapping filter_name (or None) -> FocusResult, or None if failed
        """
        if not FOCUSLOOP_AVAILABLE:
            logger.error("FocusLoop module not available")
            return None

        if not self._state.telescope_connected:
            logger.error("Cannot run focus loop: telescope not connected")
            return None

        if not self._state.camera_connected:
            logger.error("Cannot run focus loop: camera not connected")
            return None

        if self._state.camera_streaming:
            logger.error("Cannot run focus loop while streaming")
            return None

        logger.info("Starting focus loop...")

        with self._state_lock:
            self._state.focus_loop_running = True
            self._state.focus_loop_progress = 0

        self._notify_status_change()

        try:
            # Create focus loop with our hardware
            focus_loop = FocusLoop(
                camera=self.camera,
                telescope=self.telescope,
                filterwheel=self.filterwheel,
                config=config
            )

            # Store reference for abort
            self._focus_loop = focus_loop

            # Set progress callback
            if on_progress:
                focus_loop.on_progress = on_progress

            results = focus_loop.run()

            # Log results
            for filter_name, result in results.items():
                if result.success:
                    fname = filter_name or "single"
                    logger.info(f"Focus ({fname}): {result.best_focus:.2f} mm, "
                               f"FWHM: {result.best_fwhm_arcsec:.2f}\"")

            return results

        except Exception as e:
            logger.error(f"Focus loop error: {e}")
            with self._state_lock:
                self._state.add_error(f"Focus loop error: {e}")
            return None

        finally:
            self._focus_loop = None
            with self._state_lock:
                self._state.focus_loop_running = False
            self._notify_status_change()

    def abort_focus_loop(self):
        """Abort a running focus loop."""
        if hasattr(self, '_focus_loop') and self._focus_loop:
            self._focus_loop.abort()
            logger.info("Focus loop abort requested")

    # ==========================================================================
    # Callbacks
    # ==========================================================================

    def on_frame(self, callback: Callable[[np.ndarray, float, int], None]):
        """
        Register a callback for frame delivery.

        Callback signature: callback(frame, timestamp, framestamp)
        """
        with self._callback_lock:
            if callback not in self._frame_callbacks:
                self._frame_callbacks.append(callback)

    def remove_frame_callback(self, callback: Callable):
        """Remove a previously registered frame callback."""
        with self._callback_lock:
            if callback in self._frame_callbacks:
                self._frame_callbacks.remove(callback)

    def on_status_change(self, callback: Callable[['SystemState'], None]):
        """
        Register a callback for status changes.

        Callback signature: callback(state)
        """
        with self._callback_lock:
            if callback not in self._status_callbacks:
                self._status_callbacks.append(callback)

    def remove_status_callback(self, callback: Callable):
        """Remove a previously registered status callback."""
        with self._callback_lock:
            if callback in self._status_callbacks:
                self._status_callbacks.remove(callback)

    def get_display_frame(self, timeout: float = 0.1) -> Optional[np.ndarray]:
        """
        Get latest frame for display.

        This is designed for GUI use - returns the most recent frame
        from a small queue.

        Args:
            timeout: Timeout in seconds

        Returns:
            Frame data or None if no frame available
        """
        try:
            return self._display_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ==========================================================================
    # State
    # ==========================================================================

    @property
    def state(self) -> SystemState:
        """Get current system state (copy)."""
        with self._state_lock:
            # Return a copy to prevent external modification
            return SystemState(**self._state.__dict__)

    def update_status(self):
        """Update system status from hardware."""
        with self._state_lock:
            # Update camera status
            if self._state.camera_connected:
                self._state.camera_exposure = self.camera.get_exposure()
                temp = self.camera.get_property('SENSOR_TEMPERATURE')
                if temp:
                    self._state.camera_temperature = temp

            # Update telescope status
            if self._state.telescope_connected:
                self._state.telescope_focus = self.telescope.get_focus()
                pos = self.telescope.get_position()
                if pos:
                    self._state.telescope_ra = pos.ra
                    self._state.telescope_dec = pos.dec
                    self._state.telescope_ha = pos.ha
                    self._state.telescope_lst = pos.lst
                    self._state.telescope_airmass = pos.airmass
                    self._state.telescope_utc = f"{pos.utc_day} {pos.utc_time}"

            # Update filter wheel status
            if self._state.filterwheel_connected and self.filterwheel:
                self._state.current_filter = self.filterwheel.filter

            # Update acquisition status
            if self._state.is_saving:
                self._state.frames_saved = self.writer.frames_written
                self._state.frames_dropped = self.writer.frames_dropped
                self._state.cubes_saved = self.writer.cubes_written

        # Refresh telescope data in writer for next cube (outside lock)
        if self._state.is_saving and self._state.telescope_connected:
            self._update_writer_telescope_data()

        self._notify_status_change()

    # ==========================================================================
    # Private Methods
    # ==========================================================================

    def _update_writer_telescope_data(self):
        """Update the FITS writer with current telescope data."""
        if not self._state.telescope_connected:
            return

        try:
            # Get position and status from telescope
            position = self.telescope.get_position()
            status = self.telescope.get_status()

            # Convert dataclasses to dicts
            position_dict = None
            status_dict = None

            if position:
                position_dict = {
                    'ra': position.ra,
                    'dec': position.dec,
                    'ha': position.ha,
                    'lst': position.lst,
                    'airmass': position.airmass,
                    'utc_time': position.utc_time,
                    'utc_day': position.utc_day,
                }

            if status:
                status_dict = {
                    'focus_mm': status.focus_mm,
                    'tube_length_mm': status.tube_length_mm,
                    'offset_ra_arcsec': status.offset_ra_arcsec,
                    'offset_dec_arcsec': status.offset_dec_arcsec,
                    'rate_ra_arcsec_hr': status.rate_ra_arcsec_hr,
                    'rate_dec_arcsec_hr': status.rate_dec_arcsec_hr,
                    'cass_ring_angle': status.cass_ring_angle,
                    'telescope_id': status.telescope_id,
                    'utc_time': status.utc_time,
                    'utc_day': status.utc_day,
                }

            self.writer.set_telescope_data(position_dict, status_dict)

        except Exception as e:
            logger.warning(f"Could not update telescope data: {e}")

    def _on_camera_frame(self, frame: np.ndarray, timestamp: float, framestamp: int):
        """Internal handler for camera frames."""
        with self._state_lock:
            self._state.camera_frames_captured += 1

        # Forward to writer if saving
        if self._state.is_saving:
            self.writer.add_frame(frame, timestamp, framestamp)

        # Update display queue (keep only latest)
        try:
            while not self._display_queue.empty():
                try:
                    self._display_queue.get_nowait()
                except:
                    break
            self._display_queue.put_nowait(frame)
        except queue.Full:
            pass

        # Forward to external callbacks
        with self._callback_lock:
            for callback in self._frame_callbacks:
                try:
                    callback(frame, timestamp, framestamp)
                except Exception as e:
                    logger.error(f"Error in frame callback: {e}")

    def _notify_status_change(self):
        """Notify status callbacks of state change."""
        state_copy = self.state

        with self._callback_lock:
            for callback in self._status_callbacks:
                try:
                    callback(state_copy)
                except Exception as e:
                    logger.error(f"Error in status callback: {e}")

    # ==========================================================================
    # Context Manager
    # ==========================================================================

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup all connections."""
        logger.info("Cleaning up Cerberus API...")

        if self._state.is_saving:
            self.stop_saving()

        if self._state.camera_streaming:
            self.stop_streaming()

        if self._state.camera_connected:
            self.disconnect_camera()

        if self._state.telescope_connected:
            self.disconnect_telescope()

        if self._state.filterwheel_connected:
            self.disconnect_filterwheel()

        return False
