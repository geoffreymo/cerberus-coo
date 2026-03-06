# hardware/camera/controller.py
"""
Camera controller using single persistent thread for all DCAM operations.

This matches the working cerberus_gui_test.py architecture where ONE thread
handles all DCAM calls (init, warmup, capture) to avoid thread affinity issues.
"""

import logging
import threading
import time
import queue
import warnings
from datetime import datetime
from typing import Callable, Dict, Any, List, Optional

import numpy as np

from ..dcam import Dcam, Dcamapi, CAMERA_PARAMS
from ...config import get_config

# Optional astropy import for FITS writing
try:
    from astropy.io import fits
    HAS_ASTROPY = True
except ImportError:
    HAS_ASTROPY = False

logger = logging.getLogger(__name__)


class DCamLock:
    """
    Thread-safe locking for DCAM operations.

    Uses per-camera locks to allow concurrent operations on different cameras.
    """
    _capture_locks: Dict[int, threading.RLock] = {}
    _property_locks: Dict[int, threading.RLock] = {}
    _lock_creation = threading.Lock()

    @classmethod
    def _get_capture_lock(cls, camera_index: int) -> threading.RLock:
        """Get or create capture lock for a camera."""
        if camera_index not in cls._capture_locks:
            with cls._lock_creation:
                if camera_index not in cls._capture_locks:
                    cls._capture_locks[camera_index] = threading.RLock()
        return cls._capture_locks[camera_index]

    @classmethod
    def _get_property_lock(cls, camera_index: int) -> threading.RLock:
        """Get or create property lock for a camera."""
        if camera_index not in cls._property_locks:
            with cls._lock_creation:
                if camera_index not in cls._property_locks:
                    cls._property_locks[camera_index] = threading.RLock()
        return cls._property_locks[camera_index]

    @classmethod
    def acquire_capture(cls, camera_index: int = 0, timeout: float = 5.0) -> bool:
        return cls._get_capture_lock(camera_index).acquire(blocking=True, timeout=timeout)

    @classmethod
    def release_capture(cls, camera_index: int = 0):
        try:
            cls._get_capture_lock(camera_index).release()
        except RuntimeError:
            pass

    @classmethod
    def acquire_property(cls, camera_index: int = 0, timeout: float = 2.0) -> bool:
        return cls._get_property_lock(camera_index).acquire(blocking=True, timeout=timeout)

    @classmethod
    def release_property(cls, camera_index: int = 0):
        try:
            cls._get_property_lock(camera_index).release()
        except RuntimeError:
            pass


class CameraController:
    """
    Camera controller with single persistent thread for all DCAM operations.

    Matches cerberus_gui_test.py's CameraThread architecture exactly:
    - One thread does ALL DCAM operations (connect, warmup, capture)
    - Capture is controlled via flags, not by creating new threads
    """

    def __init__(self):
        """Initialize the camera controller."""
        # Load config
        config = get_config()
        self.buffer_size = config.camera.buffer_size
        self._settings = config.camera.defaults.copy()

        # Camera index (set in connect())
        self._camera_index: int = 0

        # DCAM handle (only accessed from camera thread)
        self.dcam: Optional[Dcam] = None
        self.is_connected = False

        # Thread control
        self._running = False
        self._camera_thread: Optional[threading.Thread] = None
        self._connect_event = threading.Event()
        self._connect_result = False

        # Capture state
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
        self._fps = 0.0

        # Capture timing info (for FITS headers)
        self.time_before_cap_start: Optional[float] = None
        self.time_after_cap_start: Optional[float] = None

        # Cached camera parameters
        self._camera_params: Dict[str, Any] = {}
        self._params_lock = threading.RLock()

        # Save queue (set by API when saving is enabled)
        self.save_queue: Optional[queue.Queue] = None

        # Current exposure time (for FITS headers)
        self._current_exposure: Optional[float] = None

    # === Connection (starts persistent thread) ===

    def connect(self, camera_index: int = 0) -> bool:
        """Connect to camera by starting the persistent camera thread."""
        if self._running:
            logger.warning("Camera thread already running")
            return self.is_connected

        self._camera_index = camera_index
        self._connect_event.clear()
        self._connect_result = False
        self._running = True

        # Start the persistent camera thread
        self._camera_thread = threading.Thread(
            target=self._camera_thread_main,
            name="CameraThread",
            daemon=True
        )
        self._camera_thread.start()

        # Wait for connection to complete
        if self._connect_event.wait(timeout=30.0):
            return self._connect_result
        else:
            logger.error("Timeout waiting for camera connection")
            return False

    def disconnect(self):
        """Disconnect from camera by stopping the persistent thread."""
        logger.info("Disconnecting camera...")

        # Stop capture if running
        if self._capturing:
            self.stop_streaming()

        # Signal thread to stop
        self._running = False

        # Wait for thread to finish
        if self._camera_thread and self._camera_thread.is_alive():
            self._camera_thread.join(timeout=5.0)
            if self._camera_thread.is_alive():
                logger.warning("Camera thread did not stop cleanly")

        self._camera_thread = None
        self.is_connected = False
        logger.info("Camera disconnected")

    # === Persistent Camera Thread (all DCAM ops happen here) ===

    def _camera_thread_main(self):
        """Main camera thread - ALL DCAM operations happen here."""
        logger.info("Camera thread starting")

        try:
            # Connect to camera (in this thread)
            if self._connect_camera():
                self._connect_result = True
                self._connect_event.set()

                # Run main loop
                self._main_loop()
            else:
                self._connect_result = False
                self._connect_event.set()

        except Exception as e:
            logger.error(f"Fatal error in camera thread: {e}")
            self._connect_result = False
            self._connect_event.set()
        finally:
            self._cleanup()

    def _connect_camera(self) -> bool:
        """Connect to camera (called from camera thread).

        Note: Dcamapi.init() is called once at application startup in __main__.py,
        so we don't need to init/uninit here.
        """
        retry_count = 0
        max_retries = 3

        while self._running and retry_count < max_retries:
            retry_count += 1
            logger.info(f"Camera {self._camera_index} connection attempt {retry_count}")

            try:
                # Open camera device (DCAM API already initialized at startup)
                self.dcam = Dcam(self._camera_index)
                if not self.dcam.dev_open():
                    raise RuntimeError(f"Device open failed: {self.dcam.lasterr()}")

                logger.info(f"Camera {self._camera_index} connected successfully")
                self._apply_defaults()
                self._update_camera_params()

                # Log all camera properties at debug level
                logger.debug(f"=== CAMERA {self._camera_index} PROPERTIES ===")
                with self._params_lock:
                    for prop_name in sorted(self._camera_params.keys()):
                        logger.debug(f"  {prop_name}: {self._camera_params[prop_name]}")

                self.is_connected = True

                # Perform warmup capture (in same thread!)
                self._warmup_capture()

                return True

            except Exception as e:
                logger.warning(f"Failed to open camera {self._camera_index}: {e}")
                time.sleep(2)

        return False

    def _main_loop(self):
        """Main loop - runs capture_frame when capturing, else sleeps."""
        logger.info("Camera main loop starting")

        while self._running:
            try:
                if self._capturing and not self._stop_requested.is_set():
                    self._capture_frame()
                else:
                    time.sleep(0.01)  # Short sleep when idle
            except Exception as e:
                logger.error(f"Error in camera loop: {e}")
                time.sleep(0.001)

        logger.info("Camera main loop ended")

    def _capture_frame(self):
        """Capture a single frame (matches cerberus_gui_test.py exactly)."""
        if self._stop_requested.is_set():
            return

        timeout_ms = 10  # 10ms

        if not DCamLock.acquire_capture(self._camera_index, timeout=0.005):
            return

        lock_released = False
        try:
            if not self._capturing or self._stop_requested.is_set():
                return

            # Wait for frame
            if self.dcam.wait_capevent_frameready(timeout_ms):
                frame_index_safe = self._frame_index % self.buffer_size
                result = self.dcam.buf_getframe_with_timestamp_and_framestamp(frame_index_safe)

                if result is not False:
                    frame, npBuf, timestamp, framestamp = result
                    frame_copy = np.copy(npBuf)

                    # Release lock before processing so other threads can access DCAM
                    DCamLock.release_capture(self._camera_index)
                    lock_released = True

                    # Process frame (outside lock)
                    self._process_frame(frame_copy, timestamp, framestamp)
                    return

        finally:
            if not lock_released:
                DCamLock.release_capture(self._camera_index)

    def _process_frame(self, frame: np.ndarray, timestamp, framestamp: int):
        """Process frame (matches cerberus_gui_test.py exactly)."""
        # Handle timestamp rollover (32-bit microseconds wraps at ~4295 seconds)
        raw_timestamp = timestamp.sec + timestamp.microsec / 1e6
        if raw_timestamp < self._last_raw_timestamp - 4000:
            self._timestamp_offset += 4294.967296
            logger.warning(f"Timestamp rollover at frame {self._frame_index}")
        self._last_raw_timestamp = raw_timestamp
        corrected_timestamp = raw_timestamp + self._timestamp_offset

        # Handle framestamp rollover (16-bit counter wraps at 65536)
        if framestamp < self._last_raw_framestamp - 60000:
            self._framestamp_offset += 65536
            logger.warning(f"Framestamp rollover at frame {self._frame_index}")
        self._last_raw_framestamp = framestamp
        corrected_framestamp = framestamp + self._framestamp_offset

        self._frame_count += 1

        # Calculate FPS
        current_time = time.time()
        elapsed = current_time - self._fps_calc_time
        if elapsed >= 1.0:
            self._fps = self._frame_count / elapsed
            self._frame_count = 0
            self._fps_calc_time = current_time

        # Queue for saving if enabled
        if self.save_queue is not None:
            queue_size = self.save_queue.qsize()
            max_size = self.save_queue.maxsize if hasattr(self.save_queue, 'maxsize') else 50000

            # Backpressure check
            if queue_size > max_size * 0.9:
                pass  # Drop frame
            else:
                try:
                    self.save_queue.put_nowait((frame, corrected_timestamp, corrected_framestamp))
                except queue.Full:
                    pass

        # Deliver to callbacks
        with self._callback_lock:
            for callback in self._frame_callbacks:
                try:
                    callback(frame, corrected_timestamp, corrected_framestamp)
                except Exception as e:
                    logger.error(f"Error in frame callback: {e}")

        self._frame_index += 1

    # === Streaming Control ===

    def start_streaming(self, align_to_second: bool = True) -> bool:
        """Start capture (sets flags, actual capture happens in camera thread)."""
        logger.info("Starting capture")

        if not self.is_connected:
            logger.error("Camera not connected")
            return False

        self._stop_requested.clear()

        if not DCamLock.acquire_capture(self._camera_index, timeout=3.0):
            logger.error("Failed to acquire lock")
            return False

        try:
            # Stop any existing capture
            if self._capturing:
                self._stop_capture_internal()

            if self.dcam is None:
                logger.error("Camera not initialized")
                return False

            # Allocate buffer
            logger.info(f"Allocating camera ring buffer for {self.buffer_size} frames")
            if not self.dcam.buf_alloc(self.buffer_size):
                logger.error("Buffer allocation failed")
                return False

            # Initialize capture state
            self._frame_index = 0
            self._frame_count = 0
            self._fps_calc_time = time.time()

            # Reset timestamp rollover tracking
            self._timestamp_offset = 0
            self._last_raw_timestamp = 0
            self._framestamp_offset = 0
            self._last_raw_framestamp = 0

            # Enable timestamp producer
            try:
                self.dcam.prop_setgetvalue(CAMERA_PARAMS['TIME_STAMP_PRODUCER'], 1)
            except Exception as e:
                logger.error(f"Error setting timestamp producer: {e}")

            # Align to integer second if requested
            if align_to_second:
                current_time = time.time()
                next_integer_second = int(current_time) + 1
                target_cap_start_time = next_integer_second + 0.10

                wait_time = target_cap_start_time - time.time()
                if wait_time > 0:
                    logger.info(f"Syncing: waiting {wait_time*1000:.1f}ms")
                    time.sleep(wait_time)

            self.time_before_cap_start = time.time()

            # Start capture
            if not self.dcam.cap_start():
                logger.error("Failed to start capture")
                self.dcam.buf_release()
                return False

            self.time_after_cap_start = time.time()
            logger.info(f"Capture started (cap_start took {(self.time_after_cap_start - self.time_before_cap_start)*1000:.2f}ms)")

            # Set flag - main loop will start calling capture_frame()
            self._capturing = True

            return True

        finally:
            DCamLock.release_capture(self._camera_index)

    def stop_streaming(self) -> bool:
        """Stop capture."""
        logger.info("Stopping capture")

        self._stop_requested.set()
        time.sleep(0.2)

        if DCamLock.acquire_capture(self._camera_index, timeout=2.0):
            try:
                result = self._stop_capture_internal()
            finally:
                DCamLock.release_capture(self._camera_index)
        else:
            result = self._stop_capture_internal(force=True)

        self._stop_requested.clear()
        return result

    def _stop_capture_internal(self, force=False) -> bool:
        """Internal capture stop logic."""
        self._capturing = False

        if self.dcam is not None and not force:
            try:
                if not self.dcam.cap_stop():
                    logger.error(f"cap_stop failed: {self.dcam.lasterr()}")
                if not self.dcam.buf_release():
                    logger.error(f"buf_release failed: {self.dcam.lasterr()}")

                self._reset_trigger_state()
                logger.info("Capture stopped cleanly")
            except Exception as e:
                logger.error(f"Error stopping capture: {e}")

        return True

    def is_streaming(self) -> bool:
        """Check if capture is running."""
        return self._capturing

    # === Warmup ===

    def _warmup_capture(self):
        """Perform warmup capture (in camera thread)."""
        logger.info(f"Performing warmup capture for camera {self._camera_index}...")

        if not DCamLock.acquire_capture(self._camera_index, timeout=3.0):
            logger.warning("Could not acquire lock for warmup")
            return

        try:
            if self.dcam is None:
                return

            if not self.dcam.buf_alloc(1):
                logger.warning("Warmup: Buffer allocation failed")
                return

            warmup_start = time.time()
            if not self.dcam.cap_start():
                logger.warning("Warmup: cap_start failed")
                self.dcam.buf_release()
                return

            warmup_duration = time.time() - warmup_start
            logger.info(f"Warmup: cap_start took {warmup_duration*1000:.2f}ms")

            time.sleep(0.01)

            if not self.dcam.cap_stop():
                logger.warning("Warmup: cap_stop failed")

            if not self.dcam.buf_release():
                logger.warning("Warmup: buf_release failed")

            logger.info("Warmup capture complete")

        except Exception as e:
            logger.error(f"Error during warmup: {e}")
        finally:
            DCamLock.release_capture(self._camera_index)

    # === Cleanup ===

    def _cleanup(self):
        """Clean up resources (called from camera thread).

        Note: Dcamapi.uninit() is called once at application shutdown in __main__.py,
        so we only close the individual camera device here.
        """
        logger.info(f"Camera {self._camera_index} thread cleanup starting")

        self._running = False
        self._stop_requested.set()

        if self._capturing:
            try:
                self._stop_capture_internal()
            except Exception as e:
                logger.error(f"Error stopping capture: {e}")

        if self.dcam is not None:
            try:
                self.dcam.dev_close()
                logger.info(f"Camera {self._camera_index} device closed")
            except Exception as e:
                logger.error(f"Error closing camera {self._camera_index}: {e}")
            self.dcam = None

        self.is_connected = False
        logger.info(f"Camera {self._camera_index} thread cleanup complete")

    # === Properties ===

    def set_property(self, prop_name: str, value: float) -> bool:
        """Set camera property."""
        if prop_name not in CAMERA_PARAMS:
            logger.error(f"Unknown property: {prop_name}")
            return False

        if not DCamLock.acquire_property(self._camera_index, timeout=1.0):
            logger.error(f"Failed to acquire property lock for {prop_name}")
            return False

        try:
            if self.dcam is None:
                return False

            if self.dcam.prop_setvalue(CAMERA_PARAMS[prop_name], value):
                self._update_camera_params()
                return True
            else:
                logger.error(f"Failed to set {prop_name}: {self.dcam.lasterr()}")
                return False
        finally:
            DCamLock.release_property(self._camera_index)

    def get_property(self, prop_name: str) -> Optional[float]:
        """Get camera property value."""
        if prop_name not in CAMERA_PARAMS:
            return None

        if not DCamLock.acquire_property(self._camera_index, timeout=1.0):
            return None

        try:
            if self.dcam is None:
                return None

            value = self.dcam.prop_getvalue(CAMERA_PARAMS[prop_name])
            return value if value is not False else None
        finally:
            DCamLock.release_property(self._camera_index)

    def set_exposure(self, seconds: float) -> bool:
        """Set exposure time in seconds."""
        result = self.set_property('EXPOSURE_TIME', seconds)
        if result:
            self._current_exposure = seconds
        return result

    def get_exposure(self) -> Optional[float]:
        """Get exposure time in seconds."""
        return self.get_property('EXPOSURE_TIME')

    def set_binning(self, factor: int) -> bool:
        """Set binning factor."""
        if factor not in [1, 2, 4]:
            logger.error(f"Invalid binning factor: {factor}")
            return False
        return self.set_property('BINNING', float(factor))

    def set_trigger_source(self, source: str) -> bool:
        """Set trigger source."""
        source_map = {'internal': 1.0, 'external': 2.0, 'software': 3.0}
        if source.lower() not in source_map:
            logger.error(f"Invalid trigger source: {source}")
            return False
        return self.set_property('TRIGGER_SOURCE', source_map[source.lower()])

    def get_all_params(self) -> Dict[str, Any]:
        """Get all camera parameters."""
        with self._params_lock:
            return self._camera_params.copy()

    def get_frame_rate(self) -> float:
        """Get current frame rate."""
        return self._fps

    # === Callbacks ===

    def on_frame(self, callback: Callable[[np.ndarray, float, int], None]):
        """Register frame callback."""
        with self._callback_lock:
            if callback not in self._frame_callbacks:
                self._frame_callbacks.append(callback)

    def remove_frame_callback(self, callback: Callable):
        """Remove frame callback."""
        with self._callback_lock:
            if callback in self._frame_callbacks:
                self._frame_callbacks.remove(callback)

    # === Single Capture ===

    def capture_single(self, timeout_ms: int = 30000) -> Optional[np.ndarray]:
        """Capture a single frame."""
        if not self.is_connected:
            logger.error("Camera not connected")
            return None

        if self._capturing:
            logger.error("Cannot capture single while streaming")
            return None

        if not DCamLock.acquire_capture(self._camera_index, timeout=5.0):
            logger.error("Failed to acquire capture lock")
            return None

        try:
            if not self.dcam.buf_alloc(1):
                logger.error(f"Buffer allocation failed: {self.dcam.lasterr()}")
                return None

            try:
                if not self.dcam.cap_snapshot():
                    logger.error(f"Snapshot failed: {self.dcam.lasterr()}")
                    return None

                if not self.dcam.wait_capevent_frameready(timeout_ms):
                    self.dcam.cap_stop()
                    logger.error("Timeout waiting for frame")
                    return None

                result = self.dcam.buf_getframe(0)
                if result is False:
                    self.dcam.cap_stop()
                    logger.error(f"Failed to get frame: {self.dcam.lasterr()}")
                    return None

                frame, npBuf = result
                frame_copy = np.copy(npBuf)

                self.dcam.cap_stop()
                return frame_copy

            finally:
                self.dcam.buf_release()

        finally:
            DCamLock.release_capture(self._camera_index)

    def save_fits(self, data: np.ndarray, filepath: str,
                  header_extra: dict = None, object_name: str = None):
        """Save frame data to FITS file."""
        if not HAS_ASTROPY:
            raise ImportError("astropy is required for FITS writing")

        hdu = fits.PrimaryHDU(data=data)
        hdu.header['DATE-OBS'] = datetime.utcnow().isoformat()
        hdu.header['INSTRUME'] = 'Cerberus-qCMOS'

        if self._current_exposure is not None:
            hdu.header['EXPTIME'] = (self._current_exposure, 'Exposure time in seconds')

        if object_name:
            hdu.header['OBJECT'] = object_name

        if header_extra:
            for key, value in header_extra.items():
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore")
                    try:
                        hdu.header[key] = value
                    except Exception:
                        pass

        hdu.writeto(filepath, overwrite=True)
        logger.info(f"Saved: {filepath}")

    # === Private Helpers ===

    def _apply_defaults(self):
        """Apply default camera settings."""
        defaults = {
            'READOUT_SPEED': 1.0,
            'EXPOSURE_TIME': 1.0,
            'TRIGGER_MODE': 6.0,
            'TRIGGER_SOURCE': 2.0,
            'TRIGGER_POLARITY': 2.0,
            'OUTPUT_TRIG_KIND_0': 3.0,
            'OUTPUT_TRIG_ACTIVE_0': 1.0,
            'OUTPUT_TRIG_POLARITY_0': 2.0,
            'OUTPUT_TRIG_PERIOD_0': 10.0,
            'SENSOR_MODE': 1.0,
            'IMAGE_PIXEL_TYPE': 2.0,
            'DEFECT_CORRECT_MODE': 1.0,
            'HOT_PIXEL_CORRECT_LEVEL': 2.0
        }

        # Override with config settings
        defaults.update(self._settings)

        logger.info("Setting default camera parameters")
        for prop, value in defaults.items():
            self.set_property(prop, value)

    def _update_camera_params(self):
        """Update cached camera parameters."""
        if not DCamLock.acquire_property(self._camera_index, timeout=1.0):
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
            DCamLock.release_property(self._camera_index)

    def _reset_trigger_state(self):
        """Restore trigger state after cap_stop()."""
        try:
            with self._params_lock:
                gui_trigger_text = self._camera_params.get('TRIGGER SOURCE', 'INTERNAL')

            if gui_trigger_text != 'EXTERNAL':
                return

            current = self.dcam.prop_getvalue(CAMERA_PARAMS['TRIGGER_SOURCE'])
            if current is False:
                return

            if current != 2.0:
                logger.info("Restoring trigger source to External")
                if self.dcam.prop_setvalue(CAMERA_PARAMS['TRIGGER_SOURCE'], 2.0):
                    logger.info("Trigger source restored to External")
                else:
                    logger.error(f"Failed to restore trigger: {self.dcam.lasterr()}")

        except Exception as e:
            logger.error(f"Error restoring trigger state: {e}")
