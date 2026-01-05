# hardware/camera/controller.py
"""Camera controller for Hamamatsu qCMOS via DCAM API."""

import threading
import time
import logging
import numpy as np
from typing import Callable, Optional, List, Dict, Any

from ..dcam import Dcam, Dcamapi, CAMERA_PARAMS

logger = logging.getLogger(__name__)


def _get_camera_config():
    """Lazy load camera config to avoid circular imports."""
    try:
        from ...config import get_config
        return get_config().camera
    except Exception:
        return None


class DCamLock:
    """Thread-safe locking for DCAM operations."""
    _capture_lock = threading.RLock()
    _property_lock = threading.RLock()

    @classmethod
    def acquire_capture(cls, timeout: float = 5.0) -> bool:
        return cls._capture_lock.acquire(blocking=True, timeout=timeout)

    @classmethod
    def release_capture(cls):
        try:
            cls._capture_lock.release()
        except RuntimeError:
            pass

    @classmethod
    def acquire_property(cls, timeout: float = 2.0) -> bool:
        return cls._property_lock.acquire(blocking=True, timeout=timeout)

    @classmethod
    def release_property(cls):
        try:
            cls._property_lock.release()
        except RuntimeError:
            pass


class CameraController:
    """
    Manages camera hardware and capture.

    Supports both streaming capture (for live view/saving) and
    single-frame capture (for focus loop).

    Example usage:
        controller = CameraController()
        if controller.connect():
            controller.set_exposure(0.1)  # 100ms

            # For streaming:
            controller.on_frame(my_callback)
            controller.start_streaming()
            time.sleep(10)
            controller.stop_streaming()

            # For single capture:
            frame = controller.capture_single()

            controller.disconnect()
    """

    # Default camera settings (fallback if config not available)
    DEFAULT_SETTINGS = {
        'READOUT_SPEED': 1.0,
        'EXPOSURE_TIME': 1.0,
        'TRIGGER_MODE': 6.0,      # Start mode
        'TRIGGER_SOURCE': 2.0,    # External
        'TRIGGER_POLARITY': 2.0,  # Positive edge
        'OUTPUT_TRIG_KIND_0': 3.0,
        'OUTPUT_TRIG_ACTIVE_0': 1.0,
        'OUTPUT_TRIG_POLARITY_0': 2.0,
        'OUTPUT_TRIG_PERIOD_0': 10.0,
        'SENSOR_MODE': 1.0,
        'IMAGE_PIXEL_TYPE': 2.0,
        'DEFECT_CORRECT_MODE': 1.0,
        'HOT_PIXEL_CORRECT_LEVEL': 2.0
    }

    def __init__(self, buffer_size: int = None):
        """
        Initialize camera controller.

        Args:
            buffer_size: Number of frames in capture ring buffer (uses config if None)
        """
        # Load settings from config if available
        config = _get_camera_config()
        if config:
            self._settings = dict(self.DEFAULT_SETTINGS)
            self._settings.update(config.defaults)
            buffer_size = buffer_size or config.buffer_size
        else:
            self._settings = dict(self.DEFAULT_SETTINGS)
            buffer_size = buffer_size or 100

        self.dcam: Optional[Dcam] = None
        self.is_connected: bool = False
        self.buffer_size = buffer_size

        # Capture state
        self._capture_thread: Optional[threading.Thread] = None
        self._capturing = False
        self._stop_requested = threading.Event()
        self._frame_index = 0

        # Frame callbacks
        self._frame_callbacks: List[Callable] = []
        self._callback_lock = threading.Lock()

        # Timestamp rollover tracking
        self._timestamp_offset = 0
        self._last_raw_timestamp = 0
        self._framestamp_offset = 0
        self._last_raw_framestamp = 0

        # Performance monitoring
        self._frame_count = 0
        self._fps_calc_time = time.time()

        # Capture timing info (for FITS headers)
        self.time_before_cap_start: Optional[float] = None
        self.time_after_cap_start: Optional[float] = None

        # Cached camera parameters
        self._camera_params: Dict[str, Any] = {}
        self._params_lock = threading.RLock()

    def connect(self, camera_index: int = 0) -> bool:
        """
        Connect to the camera.

        Args:
            camera_index: Camera device index (default 0)

        Returns:
            True if connection successful
        """
        if self.is_connected:
            logger.warning("Camera already connected")
            return True

        retry_count = 0
        max_retries = 3

        while retry_count < max_retries:
            retry_count += 1
            logger.info(f"Camera connection attempt {retry_count}")

            try:
                # Initialize DCAM API
                if Dcamapi.init():
                    logger.info("DCAM API initialized")
                else:
                    raise RuntimeError(f"DCAM API init failed: {Dcamapi.lasterr()}")

                # Open camera device
                self.dcam = Dcam(camera_index)
                if not self.dcam.dev_open():
                    raise RuntimeError(f"Device open failed: {self.dcam.lasterr()}")

                logger.info("Camera connected successfully")
                self._apply_defaults()
                self._update_camera_params()

                # Log all camera properties
                logger.info("=== CAMERA PROPERTIES ===")
                with self._params_lock:
                    for prop_name in sorted(self._camera_params.keys()):
                        logger.info(f"  {prop_name}: {self._camera_params[prop_name]}")

                self.is_connected = True

                # Warmup capture to initialize hardware
                self._warmup_capture()

                return True

            except Exception as e:
                logger.warning(f"Failed to open camera: {e}")
                Dcamapi.uninit()
                time.sleep(2)

        return False

    def disconnect(self):
        """Disconnect from camera and release resources."""
        if not self.is_connected:
            return

        logger.info("Disconnecting camera...")

        # Stop capture if running
        if self._capturing:
            self.stop_streaming()

        # Close camera device
        if self.dcam is not None:
            lock_acquired = DCamLock.acquire_capture(timeout=2.0)
            try:
                self.dcam.dev_close()
                logger.info("Camera device closed")
            except Exception as e:
                logger.error(f"Error closing camera: {e}")
            finally:
                if lock_acquired:
                    DCamLock.release_capture()
                self.dcam = None

        # Uninitialize DCAM API
        try:
            Dcamapi.uninit()
            logger.info("DCAM API uninitialized")
        except Exception as e:
            logger.error(f"Error uninitializing DCAM API: {e}")

        self.is_connected = False
        logger.info("Camera disconnected")

    # === Property Methods ===

    def set_exposure(self, seconds: float) -> bool:
        """Set exposure time in seconds."""
        return self.set_property('EXPOSURE_TIME', seconds)

    def get_exposure(self) -> Optional[float]:
        """Get current exposure time in seconds."""
        return self.get_property('EXPOSURE_TIME')

    def set_binning(self, factor: int) -> bool:
        """Set binning factor (1, 2, or 4)."""
        return self.set_property('BINNING', float(factor))

    def set_trigger_source(self, source: str) -> bool:
        """
        Set trigger source.

        Args:
            source: 'internal', 'external', or 'software'
        """
        values = {'internal': 1.0, 'external': 2.0, 'software': 3.0}
        value = values.get(source.lower())
        if value is None:
            logger.error(f"Invalid trigger source: {source}")
            return False
        return self.set_property('TRIGGER_SOURCE', value)

    def set_property(self, name: str, value: float) -> bool:
        """
        Set a camera property.

        Args:
            name: Property name (from CAMERA_PARAMS)
            value: Property value

        Returns:
            True if successful
        """
        if name not in CAMERA_PARAMS:
            logger.error(f"Unknown property: {name}")
            return False

        if not DCamLock.acquire_property(timeout=1.0):
            logger.error(f"Failed to acquire lock for {name}")
            return False

        try:
            if self.dcam is None:
                return False

            # Log trigger source changes
            if name == 'TRIGGER_SOURCE':
                before = self.dcam.prop_getvalue(CAMERA_PARAMS['TRIGGER_SOURCE'])
                before_text = {1.0: 'Internal', 2.0: 'External', 3.0: 'Software'}.get(before, str(before))
                value_text = {1.0: 'Internal', 2.0: 'External', 3.0: 'Software'}.get(value, str(value))
                logger.info(f"Trigger source: {before_text} -> {value_text}")

            if self.dcam.prop_setvalue(CAMERA_PARAMS[name], value):
                self._update_camera_params()
                return True
            else:
                logger.error(f"Failed to set {name}: {self.dcam.lasterr()}")
                return False
        finally:
            DCamLock.release_property()

    def get_property(self, name: str) -> Optional[float]:
        """
        Get a camera property value.

        Args:
            name: Property name (from CAMERA_PARAMS)

        Returns:
            Property value or None if failed
        """
        if name not in CAMERA_PARAMS:
            logger.error(f"Unknown property: {name}")
            return None

        if not DCamLock.acquire_property(timeout=1.0):
            return None

        try:
            if self.dcam is None:
                return None

            value = self.dcam.prop_getvalue(CAMERA_PARAMS[name])
            return value if value is not False else None
        finally:
            DCamLock.release_property()

    def get_all_params(self) -> Dict[str, Any]:
        """Get a copy of all camera parameters."""
        with self._params_lock:
            return dict(self._camera_params)

    # === Single Capture ===

    def capture_single(self, timeout_ms: int = 30000) -> Optional[np.ndarray]:
        """
        Capture a single frame.

        This method is blocking and designed for use in focus loops
        or other single-exposure scenarios.

        Args:
            timeout_ms: Timeout in milliseconds

        Returns:
            Frame data as numpy array, or None if failed
        """
        if not self.is_connected or self.dcam is None:
            logger.error("Camera not connected")
            return None

        if self._capturing:
            logger.error("Cannot capture single frame while streaming")
            return None

        if not DCamLock.acquire_capture(timeout=3.0):
            logger.error("Failed to acquire capture lock")
            return None

        try:
            # Allocate minimal buffer
            if not self.dcam.buf_alloc(1):
                logger.error("Buffer allocation failed")
                return None

            # Start capture
            if not self.dcam.cap_start():
                logger.error("Failed to start capture")
                self.dcam.buf_release()
                return None

            # Wait for frame
            if not self.dcam.wait_capevent_frameready(timeout_ms):
                logger.error("Timeout waiting for frame")
                self.dcam.cap_stop()
                self.dcam.buf_release()
                return None

            # Get frame data
            result = self.dcam.buf_getframe(0)
            if result is False:
                logger.error("Failed to get frame data")
                self.dcam.cap_stop()
                self.dcam.buf_release()
                return None

            frame, npBuf = result
            frame_copy = np.copy(npBuf)

            # Stop capture and release buffer
            self.dcam.cap_stop()
            self.dcam.buf_release()

            return frame_copy

        except Exception as e:
            logger.error(f"Error in single capture: {e}")
            try:
                self.dcam.cap_stop()
                self.dcam.buf_release()
            except:
                pass
            return None
        finally:
            DCamLock.release_capture()

    # === Streaming Capture ===

    def start_streaming(self, align_to_second: bool = True) -> bool:
        """
        Start continuous frame capture.

        Frames will be delivered to registered callbacks.

        Args:
            align_to_second: If True, align capture start to next integer second

        Returns:
            True if streaming started successfully
        """
        if not self.is_connected or self.dcam is None:
            logger.error("Camera not connected")
            return False

        if self._capturing:
            logger.warning("Already streaming")
            return True

        logger.info("Starting streaming capture")
        self._stop_requested.clear()

        if not DCamLock.acquire_capture(timeout=3.0):
            logger.error("Failed to acquire capture lock")
            return False

        try:
            # Allocate buffer
            logger.info(f"Allocating ring buffer for {self.buffer_size} frames")
            if not self.dcam.buf_alloc(self.buffer_size):
                logger.error("Buffer allocation failed")
                return False

            # Initialize capture state
            self._capturing = True
            self._frame_index = 0
            self._frame_count = 0
            self._fps_calc_time = time.time()

            # Reset timestamp tracking
            self._timestamp_offset = 0
            self._last_raw_timestamp = 0
            self._framestamp_offset = 0
            self._last_raw_framestamp = 0

            # Enable timestamp producer
            try:
                self.dcam.prop_setgetvalue(CAMERA_PARAMS['TIME_STAMP_PRODUCER'], 1)
            except Exception as e:
                logger.warning(f"Could not enable timestamp producer: {e}")

            # Align to next integer second if requested
            if align_to_second:
                current_time = time.time()
                next_second = int(current_time) + 1
                target_time = next_second + 0.10  # 100ms after integer second
                wait_time = target_time - time.time()
                if wait_time > 0:
                    logger.info(f"Aligning capture start (waiting {wait_time*1000:.1f}ms)")
                    time.sleep(wait_time)

            # Record timing
            self.time_before_cap_start = time.time()

            # Start capture
            if not self.dcam.cap_start():
                logger.error("Failed to start capture")
                self._capturing = False
                self.dcam.buf_release()
                return False

            self.time_after_cap_start = time.time()
            cap_duration = (self.time_after_cap_start - self.time_before_cap_start) * 1000
            logger.info(f"Capture started (cap_start took {cap_duration:.1f}ms)")

            # Start capture thread
            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                name="CameraCapture",
                daemon=True
            )
            self._capture_thread.start()

            return True

        finally:
            DCamLock.release_capture()

    def stop_streaming(self) -> bool:
        """Stop continuous frame capture."""
        if not self._capturing:
            return True

        logger.info("Stopping streaming capture")
        self._stop_requested.set()

        # Wait for capture thread to finish
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=2.0)
            self._capture_thread = None

        if DCamLock.acquire_capture(timeout=2.0):
            try:
                self._capturing = False
                if self.dcam is not None:
                    self.dcam.cap_stop()
                    self.dcam.buf_release()
                    self._restore_trigger_state()
                logger.info("Capture stopped")
            finally:
                DCamLock.release_capture()
        else:
            self._capturing = False
            logger.warning("Could not acquire lock for clean stop")

        self._stop_requested.clear()
        return True

    def is_streaming(self) -> bool:
        """Check if currently streaming."""
        return self._capturing

    # === Callbacks ===

    def on_frame(self, callback: Callable[[np.ndarray, float, int], None]):
        """
        Register a callback for frame delivery.

        Callback signature: callback(frame, timestamp, framestamp)
        - frame: numpy array with image data
        - timestamp: camera timestamp in seconds
        - framestamp: frame counter from camera
        """
        with self._callback_lock:
            if callback not in self._frame_callbacks:
                self._frame_callbacks.append(callback)

    def remove_frame_callback(self, callback: Callable):
        """Remove a previously registered callback."""
        with self._callback_lock:
            if callback in self._frame_callbacks:
                self._frame_callbacks.remove(callback)

    # === Private Methods ===

    def _capture_loop(self):
        """Main capture loop (runs in separate thread)."""
        timeout_ms = 10

        while self._capturing and not self._stop_requested.is_set():
            try:
                if not DCamLock.acquire_capture(timeout=0.005):
                    continue

                try:
                    if not self._capturing or self._stop_requested.is_set():
                        break

                    # Wait for frame
                    if self.dcam.wait_capevent_frameready(timeout_ms):
                        frame_idx = self._frame_index % self.buffer_size
                        result = self.dcam.buf_getframe_with_timestamp_and_framestamp(frame_idx)

                        if result is not False:
                            frame, npBuf, timestamp, framestamp = result
                            frame_copy = np.copy(npBuf)

                            DCamLock.release_capture()

                            # Process frame
                            self._process_frame(frame_copy, timestamp, framestamp)
                            continue

                finally:
                    DCamLock.release_capture()

            except Exception as e:
                logger.error(f"Error in capture loop: {e}")
                time.sleep(0.001)

    def _process_frame(self, frame: np.ndarray, timestamp, framestamp: int):
        """Process captured frame and deliver to callbacks."""
        # Handle timestamp rollover
        raw_timestamp = timestamp.sec + timestamp.microsec / 1e6
        if raw_timestamp < self._last_raw_timestamp - 4000:
            self._timestamp_offset += 4294.967296
            logger.warning(f"Timestamp rollover at frame {self._frame_index}")
        self._last_raw_timestamp = raw_timestamp
        corrected_timestamp = raw_timestamp + self._timestamp_offset

        # Handle framestamp rollover
        if framestamp < self._last_raw_framestamp - 60000:
            self._framestamp_offset += 65536
            logger.warning(f"Framestamp rollover at frame {self._frame_index}")
        self._last_raw_framestamp = framestamp
        corrected_framestamp = framestamp + self._framestamp_offset

        self._frame_count += 1
        self._frame_index += 1

        # Deliver to callbacks
        with self._callback_lock:
            for callback in self._frame_callbacks:
                try:
                    callback(frame, corrected_timestamp, corrected_framestamp)
                except Exception as e:
                    logger.error(f"Error in frame callback: {e}")

    def _apply_defaults(self):
        """Apply default camera settings (from config or fallback)."""
        logger.info("Applying default camera settings")
        for prop, value in self._settings.items():
            self.set_property(prop, value)

    def _warmup_capture(self):
        """Perform warmup capture to initialize camera hardware."""
        logger.info("Performing warmup capture...")

        if not DCamLock.acquire_capture(timeout=3.0):
            logger.warning("Could not acquire lock for warmup")
            return

        try:
            if self.dcam is None:
                return

            if not self.dcam.buf_alloc(1):
                logger.warning("Warmup: buffer allocation failed")
                return

            start = time.time()
            if not self.dcam.cap_start():
                logger.warning("Warmup: cap_start failed")
                self.dcam.buf_release()
                return

            duration = (time.time() - start) * 1000
            logger.info(f"Warmup: cap_start took {duration:.1f}ms")

            time.sleep(0.01)
            self.dcam.cap_stop()
            self.dcam.buf_release()

            logger.info("Warmup complete")

        except Exception as e:
            logger.error(f"Warmup error: {e}")
        finally:
            DCamLock.release_capture()

    def _update_camera_params(self):
        """Update cached camera parameters."""
        if not DCamLock.acquire_property(timeout=1.0):
            return

        try:
            if self.dcam is None:
                return

            with self._params_lock:
                self._camera_params.clear()
                idprop = self.dcam.prop_getnextid(0)
                while idprop is not False:
                    propname = self.dcam.prop_getname(idprop)
                    if propname:
                        propvalue = self.dcam.prop_getvalue(idprop)
                        if propvalue is not False:
                            valuetext = self.dcam.prop_getvaluetext(idprop, propvalue)
                            self._camera_params[propname] = valuetext or propvalue
                    idprop = self.dcam.prop_getnextid(idprop)
        finally:
            DCamLock.release_property()

    def _restore_trigger_state(self):
        """Restore trigger state after stopping capture."""
        try:
            with self._params_lock:
                gui_trigger = self._camera_params.get('TRIGGER SOURCE', 'INTERNAL')

            if gui_trigger != 'EXTERNAL':
                return

            current = self.dcam.prop_getvalue(CAMERA_PARAMS['TRIGGER_SOURCE'])
            if current is False:
                return

            if current != 2.0:
                logger.info("Restoring trigger source to External")
                if self.dcam.prop_setvalue(CAMERA_PARAMS['TRIGGER_SOURCE'], 2.0):
                    verify = self.dcam.prop_getvalue(CAMERA_PARAMS['TRIGGER_SOURCE'])
                    if verify == 2.0:
                        logger.info("Trigger source restored to External")
                    else:
                        logger.error(f"Trigger restore failed (got {verify})")

        except Exception as e:
            logger.error(f"Error restoring trigger: {e}")

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
        return False
