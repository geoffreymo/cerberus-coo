"""
Minimal camera interface for automated focus loop and scripted observations.

Provides a simple API for single exposures, suitable for focus loops
and scripted observations.
"""

import threading
import time
import numpy as np
from datetime import datetime
import logging
import warnings

from ..hardware.dcam import Dcam, Dcamapi, CAMERA_PARAMS

# Optional astropy import for FITS writing
try:
    from astropy.io import fits
    HAS_ASTROPY = True
except ImportError:
    HAS_ASTROPY = False


class DCamLock:
    """Thread-safe locking for DCAM operations."""
    _capture_lock = threading.RLock()
    _property_lock = threading.RLock()

    @classmethod
    def acquire_capture(cls, timeout=5.0):
        return cls._capture_lock.acquire(blocking=True, timeout=timeout)

    @classmethod
    def release_capture(cls):
        try:
            cls._capture_lock.release()
        except RuntimeError:
            pass

    @classmethod
    def acquire_property(cls, timeout=2.0):
        return cls._property_lock.acquire(blocking=True, timeout=timeout)

    @classmethod
    def release_property(cls):
        try:
            cls._property_lock.release()
        except RuntimeError:
            pass


class CerberusCamera:
    """
    Simplified camera interface for single exposures.

    This class provides a minimal API for taking single exposures
    and saving them to FITS files, suitable for focus loops and
    scripted observations.

    Usage:
        with CerberusCamera() as cam:
            cam.set_exposure(1.0)  # 1 second
            frame = cam.capture_single()
            cam.save_fits(frame, "focus_37.5_20250102.fits")

        # Or for multiple exposures:
        with CerberusCamera() as cam:
            cam.set_exposure(5.0)
            for i in range(10):
                frame = cam.capture_single()
                cam.save_fits(frame, f"image_{i:04d}.fits")
    """

    def __init__(self, device_id: int = 0):
        """
        Initialize CerberusCamera.

        Args:
            device_id: DCAM device ID (default 0 for first camera)
        """
        self.device_id = device_id
        self.dcam = None
        self.is_connected = False
        self.logger = logging.getLogger(__name__)
        self._current_exposure = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def connect(self) -> bool:
        """
        Initialize DCAM and connect to camera.

        Returns:
            True if connection successful

        Raises:
            RuntimeError: If connection fails
        """
        if self.is_connected:
            return True

        self.logger.info("Connecting to camera...")

        # Initialize DCAM API
        if not Dcamapi.init():
            raise RuntimeError(f"DCAM API init failed: {Dcamapi.lasterr()}")

        # Open camera device
        self.dcam = Dcam(self.device_id)
        if not self.dcam.dev_open():
            Dcamapi.uninit()
            raise RuntimeError(f"Device open failed: {self.dcam.lasterr()}")

        self._set_defaults()
        self.is_connected = True
        self.logger.info("Camera connected successfully")
        return True

    def disconnect(self):
        """Close camera connection."""
        if self.dcam is not None:
            try:
                self.dcam.dev_close()
            except Exception as e:
                self.logger.warning(f"Error closing device: {e}")
            self.dcam = None

        try:
            Dcamapi.uninit()
        except Exception as e:
            self.logger.warning(f"Error uninitializing DCAM: {e}")

        self.is_connected = False
        self.logger.info("Camera disconnected")

    def _set_defaults(self):
        """Set default camera parameters for focus imaging."""
        defaults = {
            'READOUT_SPEED': 1.0,        # Ultra Quiet
            'TRIGGER_SOURCE': 1.0,       # Internal
            'TRIGGER_MODE': 1.0,         # Normal
            'SENSOR_MODE': 1.0,          # Standard
            'IMAGE_PIXEL_TYPE': 2.0,     # 16-bit
            'BINNING': 1.0,              # 1x1
        }
        for prop, value in defaults.items():
            try:
                self.set_property(prop, value)
            except Exception as e:
                self.logger.warning(f"Could not set default {prop}: {e}")

    def set_property(self, prop_name: str, value: float) -> bool:
        """
        Set a camera property.

        Args:
            prop_name: Property name (from CAMERA_PARAMS)
            value: Property value

        Returns:
            True if successful

        Raises:
            ValueError: If property name unknown
            RuntimeError: If setting fails
        """
        if prop_name not in CAMERA_PARAMS:
            raise ValueError(f"Unknown property: {prop_name}")

        if not DCamLock.acquire_property(timeout=2.0):
            raise RuntimeError(f"Failed to acquire property lock for {prop_name}")

        try:
            if self.dcam is None:
                raise RuntimeError("Camera not connected")

            if self.dcam.prop_setvalue(CAMERA_PARAMS[prop_name], value):
                return True
            else:
                raise RuntimeError(f"Failed to set {prop_name}: {self.dcam.lasterr()}")
        finally:
            DCamLock.release_property()

    def get_property(self, prop_name: str) -> float:
        """
        Get a camera property value.

        Args:
            prop_name: Property name (from CAMERA_PARAMS)

        Returns:
            Property value
        """
        if prop_name not in CAMERA_PARAMS:
            raise ValueError(f"Unknown property: {prop_name}")

        if not DCamLock.acquire_property(timeout=2.0):
            raise RuntimeError("Failed to acquire property lock")

        try:
            if self.dcam is None:
                raise RuntimeError("Camera not connected")

            value = self.dcam.prop_getvalue(CAMERA_PARAMS[prop_name])
            if value is False:
                raise RuntimeError(f"Failed to get {prop_name}: {self.dcam.lasterr()}")
            return value
        finally:
            DCamLock.release_property()

    def set_exposure(self, seconds: float):
        """
        Set exposure time in seconds.

        Args:
            seconds: Exposure time in seconds
        """
        self.set_property('EXPOSURE_TIME', seconds)
        self._current_exposure = seconds
        self.logger.info(f"Exposure set to {seconds} seconds")

    def get_exposure(self) -> float:
        """
        Get current exposure time in seconds.

        Returns:
            Exposure time in seconds
        """
        return self.get_property('EXPOSURE_TIME')

    def set_binning(self, binning: int):
        """
        Set camera binning.

        Args:
            binning: Binning factor (1, 2, or 4)
        """
        if binning not in [1, 2, 4]:
            raise ValueError("Binning must be 1, 2, or 4")
        self.set_property('BINNING', float(binning))
        self.logger.info(f"Binning set to {binning}x{binning}")

    def capture_single(self, timeout_ms: int = 30000) -> np.ndarray:
        """
        Capture a single frame.

        This uses snapshot mode for a single clean exposure.

        Args:
            timeout_ms: Timeout in milliseconds (default 30s)

        Returns:
            numpy.ndarray: 2D image array

        Raises:
            RuntimeError: If capture fails
        """
        if not self.is_connected:
            raise RuntimeError("Camera not connected")

        if not DCamLock.acquire_capture(timeout=5.0):
            raise RuntimeError("Failed to acquire capture lock")

        try:
            # Allocate single frame buffer
            if not self.dcam.buf_alloc(1):
                raise RuntimeError(f"Buffer allocation failed: {self.dcam.lasterr()}")

            try:
                # Start snapshot capture (captures exactly 1 frame)
                if not self.dcam.cap_snapshot():
                    raise RuntimeError(f"Failed to start capture: {self.dcam.lasterr()}")

                # Wait for frame
                if not self.dcam.wait_capevent_frameready(timeout_ms):
                    self.dcam.cap_stop()
                    raise RuntimeError("Timeout waiting for frame")

                # Get frame data
                result = self.dcam.buf_getframe(0)
                if result is False:
                    self.dcam.cap_stop()
                    raise RuntimeError(f"Failed to get frame: {self.dcam.lasterr()}")

                frame, npBuf = result
                frame_copy = np.copy(npBuf)

                # Stop capture
                self.dcam.cap_stop()

                self.logger.debug(f"Captured frame: shape={frame_copy.shape}, dtype={frame_copy.dtype}")
                return frame_copy

            finally:
                # Always release buffer
                self.dcam.buf_release()

        finally:
            DCamLock.release_capture()

    def capture_with_timestamp(self, timeout_ms: int = 30000) -> tuple:
        """
        Capture a single frame with timestamp.

        Args:
            timeout_ms: Timeout in milliseconds

        Returns:
            Tuple of (frame, timestamp_sec, framestamp)
        """
        if not self.is_connected:
            raise RuntimeError("Camera not connected")

        if not DCamLock.acquire_capture(timeout=5.0):
            raise RuntimeError("Failed to acquire capture lock")

        try:
            # Enable timestamp producer
            try:
                self.dcam.prop_setgetvalue(CAMERA_PARAMS['TIME_STAMP_PRODUCER'], 1)
            except Exception:
                pass  # May not be available on all cameras

            if not self.dcam.buf_alloc(1):
                raise RuntimeError(f"Buffer allocation failed: {self.dcam.lasterr()}")

            try:
                if not self.dcam.cap_snapshot():
                    raise RuntimeError(f"Failed to start capture: {self.dcam.lasterr()}")

                if not self.dcam.wait_capevent_frameready(timeout_ms):
                    self.dcam.cap_stop()
                    raise RuntimeError("Timeout waiting for frame")

                result = self.dcam.buf_getframe_with_timestamp_and_framestamp(0)
                if result is False:
                    self.dcam.cap_stop()
                    raise RuntimeError(f"Failed to get frame: {self.dcam.lasterr()}")

                frame, npBuf, timestamp, framestamp = result
                frame_copy = np.copy(npBuf)
                timestamp_sec = timestamp.sec + timestamp.microsec / 1e6

                self.dcam.cap_stop()

                return frame_copy, timestamp_sec, framestamp

            finally:
                self.dcam.buf_release()

        finally:
            DCamLock.release_capture()

    def save_fits(self, data: np.ndarray, filepath: str,
                  header_extra: dict = None, object_name: str = None):
        """
        Save frame data to FITS file.

        Args:
            data: 2D numpy array
            filepath: Output path
            header_extra: Dict of additional header keywords
            object_name: Object name for OBJECT keyword
        """
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
                        if isinstance(value, tuple):
                            hdu.header[key] = value
                        else:
                            hdu.header[key] = value
                    except Exception:
                        pass

        hdu.writeto(filepath, overwrite=True)
        self.logger.info(f"Saved: {filepath}")

    def get_camera_info(self) -> dict:
        """
        Get current camera parameters.

        Returns:
            Dict of parameter names to values
        """
        if not self.is_connected:
            return {}

        if not DCamLock.acquire_property(timeout=2.0):
            return {}

        try:
            info = {}
            for name, prop_id in CAMERA_PARAMS.items():
                try:
                    value = self.dcam.prop_getvalue(prop_id)
                    if value is not False:
                        info[name] = value
                except Exception:
                    pass
            return info
        finally:
            DCamLock.release_property()
