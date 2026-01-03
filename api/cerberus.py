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

        Args:
            config_path: Path to filter configuration JSON

        Returns:
            True if successful
        """
        if not FILTERWHEEL_AVAILABLE:
            logger.error("FilterWheel module not available")
            return False

        try:
            logger.info("Connecting to filter wheel...")
            self.filterwheel = FilterWheel(config_path=config_path)

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

    def set_filter(self, name: str) -> bool:
        """
        Set filter by name.

        Args:
            name: Filter name

        Returns:
            True if successful
        """
        if self.filterwheel is None:
            logger.error("Filter wheel not connected")
            return False

        try:
            self.filterwheel.filter = name
            self.filterwheel.wait_for_move()

            with self._state_lock:
                self._state.current_filter = name

            self._notify_status_change()
            return True

        except Exception as e:
            logger.error(f"Failed to set filter: {e}")
            return False

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

    # ==========================================================================
    # Focus Loop
    # ==========================================================================

    def run_focus_loop(self, config: 'FocusLoopConfig' = None) -> Optional['FocusResult']:
        """
        Run automated focus loop.

        Args:
            config: Focus loop configuration

        Returns:
            FocusResult with optimal focus, or None if failed
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
                config=config
            )

            result = focus_loop.run()

            if result and result.success:
                logger.info(f"Focus loop complete: optimal focus = {result.optimal_focus:.2f} mm")
                # Apply optimal focus
                self.set_focus(result.optimal_focus)
            else:
                logger.warning("Focus loop did not find optimal focus")

            return result

        except Exception as e:
            logger.error(f"Focus loop error: {e}")
            with self._state_lock:
                self._state.add_error(f"Focus loop error: {e}")
            return None

        finally:
            with self._state_lock:
                self._state.focus_loop_running = False
            self._notify_status_change()

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

            # Update filter wheel status
            if self._state.filterwheel_connected and self.filterwheel:
                self._state.current_filter = self.filterwheel.filter

            # Update acquisition status
            if self._state.is_saving:
                self._state.frames_saved = self.writer.frames_written
                self._state.frames_dropped = self.writer.frames_dropped
                self._state.cubes_saved = self.writer.cubes_written

        self._notify_status_change()

    # ==========================================================================
    # Private Methods
    # ==========================================================================

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
