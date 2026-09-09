"""
Simulated camera controller.

Drop-in replacement for `hardware.camera.CameraController` with the same
public surface used by `CerberusAPI`. Frames are rendered by `SkyModel` in a
background thread at the rate implied by the exposure time.
"""

import logging
import math
import queue
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from ..config import get_config
from .sky import SkyModel
from .world import SimWorld

logger = logging.getLogger(__name__)

SENSOR_WIDTH = 4096
SENSOR_HEIGHT = 2304
READOUT_TIME_S = 0.0055

# Enumerated property values -> DCAM-style value text
_ENUM_TEXT = {
    'READOUT_SPEED': {1.0: 'ULTRA QUIET', 2.0: 'STANDARD'},
    'SENSOR_MODE': {1.0: 'AREA', 12.0: 'PHOTON NUMBER RESOLVING'},
    'TRIGGER_SOURCE': {1.0: 'INTERNAL', 2.0: 'EXTERNAL', 3.0: 'SOFTWARE'},
    'TRIGGER_MODE': {1.0: 'NORMAL', 6.0: 'START'},
    'TRIGGER_POLARITY': {1.0: 'NEGATIVE', 2.0: 'POSITIVE'},
    'TRIGGER_ACTIVE': {1.0: 'EDGE', 2.0: 'LEVEL', 3.0: 'SYNCREADOUT'},
    'DEFECT_CORRECT_MODE': {1.0: 'OFF', 2.0: 'ON'},
    'HOT_PIXEL_CORRECT_LEVEL': {1.0: 'STANDARD', 2.0: 'MINIMUM', 3.0: 'AGGRESSIVE'},
    'SUBARRAY_MODE': {1.0: 'OFF', 2.0: 'ON'},
    'IMAGE_PIXEL_TYPE': {1.0: 'MONO8', 2.0: 'MONO16'},
    'BINNING': {1.0: '1x1', 2.0: '2x2', 4.0: '4x4'},
    'OUTPUT_TRIG_KIND_0': {1.0: 'LOW', 2.0: 'EXPOSURE', 3.0: 'PROGRAMABLE', 4.0: 'TRIGGER READY', 5.0: 'HIGH'},
    'OUTPUT_TRIG_SOURCE_0': {1.0: 'EXPOSURE', 2.0: 'READOUT END', 3.0: 'VSYNC', 6.0: 'TRIGGER'},
    'OUTPUT_TRIG_ACTIVE_0': {1.0: 'EDGE', 2.0: 'LEVEL'},
    'OUTPUT_TRIG_POLARITY_0': {1.0: 'NEGATIVE', 2.0: 'POSITIVE'},
    'TIME_STAMP_PRODUCER': {1.0: 'NONE', 2.0: 'DCAM MODULE', 3.0: 'KERNEL DRIVER',
                            4.0: 'CAPTURE DEVICE', 5.0: 'IMAGING DEVICE'},
    'COLORTYPE': {1.0: 'B/W'},
}

_DEFAULT_PROPS = {
    'READOUT_SPEED': 1.0,
    'EXPOSURE_TIME': 1.0,
    'TRIGGER_MODE': 6.0,
    'TRIGGER_SOURCE': 2.0,
    'TRIGGER_POLARITY': 2.0,
    'TRIGGER_ACTIVE': 1.0,
    'OUTPUT_TRIG_KIND_0': 3.0,
    'OUTPUT_TRIG_SOURCE_0': 2.0,
    'OUTPUT_TRIG_ACTIVE_0': 1.0,
    'OUTPUT_TRIG_POLARITY_0': 1.0,
    'OUTPUT_TRIG_PERIOD_0': 5e-6,
    'SENSOR_MODE': 1.0,
    'IMAGE_PIXEL_TYPE': 2.0,
    'DEFECT_CORRECT_MODE': 1.0,
    'HOT_PIXEL_CORRECT_LEVEL': 2.0,
    'BINNING': 1.0,
    'SUBARRAY_MODE': 1.0,
    'SUBARRAY_HPOS': 0.0,
    'SUBARRAY_VPOS': 0.0,
    'TIME_STAMP_PRODUCER': 5.0,
    'COLORTYPE': 1.0,
    'BIT_PER_CHANNEL': 16.0,
    'CONVERSION_FACTOR_COEFF': 0.105,
    'CONVERSION_FACTOR_OFFSET': 200.0,
    'IMAGE_DETECTOR_PIXEL_WIDTH': 4.6,
    'IMAGE_DETECTOR_PIXEL_HEIGHT': 4.6,
}


class _CamTimestamp:
    """Mimics the DCAM timestamp struct (sec + microsec)."""
    __slots__ = ('sec', 'microsec')

    def __init__(self, t: float):
        self.sec = int(t)
        self.microsec = int((t - self.sec) * 1e6)


class SimCameraController:
    """Simulated Hamamatsu qCMOS controller."""

    def __init__(self, world: SimWorld, sensor_size=None, connect_delay_s: float = 0.6):
        config = get_config()
        self.world = world
        self.buffer_size = config.camera.buffer_size
        self._settings = dict(config.camera.defaults)
        self._connect_delay = connect_delay_s

        w, h = sensor_size or (SENSOR_WIDTH, SENSOR_HEIGHT)
        self.sensor_width = int(w)
        self.sensor_height = int(h)

        self._camera_index = 0
        self.is_connected = False
        self._props: Dict[str, float] = dict(_DEFAULT_PROPS)
        self._props['SUBARRAY_HSIZE'] = float(self.sensor_width)
        self._props['SUBARRAY_VSIZE'] = float(self.sensor_height)
        self._props_lock = threading.RLock()
        self._sky: Optional[SkyModel] = None

        # Streaming
        self._capturing = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_requested = threading.Event()
        self._frame_index = 0
        self._frame_count = 0
        self._fps = 0.0
        self._fps_calc_time = time.time()
        self._stream_t0 = 0.0

        # Callbacks / queues
        self._frame_callbacks: List[Callable] = []
        self._callback_lock = threading.Lock()
        self.save_queue: Optional[queue.Queue] = None
        self._current_exposure: Optional[float] = None

        # GPS
        self._gps_device = None
        self._gps_start_timestamp = None

        self.time_before_cap_start: Optional[float] = None
        self.time_after_cap_start: Optional[float] = None

    # === Connection ===

    def connect(self, camera_index: int = 0) -> bool:
        if self.is_connected:
            return True
        self._camera_index = camera_index
        logger.info(f"[SIM] Connecting simulated camera {camera_index}")
        time.sleep(self._connect_delay)
        self._sky = SkyModel(self.world, self.sensor_width, self.sensor_height, seed=camera_index)
        self._apply_defaults()
        self.is_connected = True
        self._running = True
        self._thread = threading.Thread(target=self._main_loop, name=f"SimCamera{camera_index}", daemon=True)
        self._thread.start()
        logger.info(f"[SIM] Camera {camera_index} connected ({self.sensor_width}x{self.sensor_height})")
        return True

    def disconnect(self):
        if self._capturing:
            self.stop_streaming()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self.is_connected = False
        logger.info(f"[SIM] Camera {self._camera_index} disconnected")

    # === Streaming ===

    def _frame_period(self) -> float:
        exp = float(self._props.get('EXPOSURE_TIME', 1.0))
        return max(exp, 1e-4) + READOUT_TIME_S

    def _main_loop(self):
        next_frame_time = None
        while self._running:
            if self._capturing and not self._stop_requested.is_set():
                period = self._frame_period()
                if next_frame_time is None:
                    next_frame_time = self._stream_t0 + period
                wait = next_frame_time - time.time()
                if wait > 0:
                    # Sleep in small chunks so stop is responsive
                    time.sleep(min(wait, 0.05))
                    continue
                try:
                    frame = self._render_frame()
                    self._process_frame(frame, next_frame_time - self._stream_t0, self._frame_index)
                except Exception as e:
                    logger.error(f"[SIM] Frame render error: {e}")
                next_frame_time += period
                # If we've fallen far behind (e.g. big exposure change), resync
                if time.time() - next_frame_time > 2 * period:
                    next_frame_time = time.time() + period
            else:
                next_frame_time = None
                time.sleep(0.01)

    def _render_frame(self) -> np.ndarray:
        with self._props_lock:
            p = dict(self._props)
        sub = p['SUBARRAY_MODE'] == 2.0
        hpos = int(p['SUBARRAY_HPOS']) if sub else 0
        vpos = int(p['SUBARRAY_VPOS']) if sub else 0
        hsize = int(p['SUBARRAY_HSIZE']) if sub else self.sensor_width
        vsize = int(p['SUBARRAY_VSIZE']) if sub else self.sensor_height
        return self._sky.render(
            exposure_s=float(p['EXPOSURE_TIME']),
            hpos=hpos, vpos=vpos, hsize=hsize, vsize=vsize,
            binning=int(p['BINNING']),
            defect_correct=(p['DEFECT_CORRECT_MODE'] == 2.0),
        )

    def _process_frame(self, frame: np.ndarray, cam_time: float, framestamp: int):
        gps_unix = None
        if self._gps_device is not None:
            ts = self._gps_device.get_timestamp()
            if ts is not None:
                gps_unix = ts.unix_seconds
                if self._gps_start_timestamp is None:
                    self._gps_start_timestamp = ts

        if self.save_queue is not None:
            try:
                self.save_queue.put_nowait((frame, cam_time, framestamp, gps_unix))
            except queue.Full:
                pass

        self._frame_count += 1
        now = time.time()
        elapsed = now - self._fps_calc_time
        if elapsed >= 1.0:
            self._fps = self._frame_count / elapsed
            self._frame_count = 0
            self._fps_calc_time = now

        with self._callback_lock:
            callbacks = list(self._frame_callbacks)
        for cb in callbacks:
            try:
                cb(frame, cam_time, framestamp)
            except Exception as e:
                logger.error(f"[SIM] Error in frame callback: {e}")

        self._frame_index += 1

    def start_streaming(self, align_to_second: bool = True) -> bool:
        if not self.is_connected:
            logger.error("[SIM] Camera not connected")
            return False
        if self._capturing:
            self._stop_capture_internal()
        self._stop_requested.clear()
        self._frame_index = 0
        self._frame_count = 0
        self._fps = 0.0
        self._fps_calc_time = time.time()
        self._gps_start_timestamp = None

        if align_to_second:
            target = int(time.time()) + 1 + 0.10
            wait = target - time.time()
            if wait > 0:
                time.sleep(wait)
        if self._gps_device is not None:
            self._gps_device.clear_buffer()

        self.time_before_cap_start = time.time()
        self._stream_t0 = time.time()
        self.time_after_cap_start = time.time()
        self._capturing = True
        logger.info(f"[SIM] Capture started on camera {self._camera_index} "
                    f"({1.0 / self._frame_period():.2f} fps)")
        return True

    def stop_streaming(self) -> bool:
        self._stop_requested.set()
        self._stop_capture_internal()
        self._stop_requested.clear()
        logger.info(f"[SIM] Capture stopped on camera {self._camera_index}")
        return True

    def _stop_capture_internal(self, force=False) -> bool:
        self._capturing = False
        time.sleep(0.05)
        return True

    def is_streaming(self) -> bool:
        return self._capturing

    # === GPS ===

    def set_gps_device(self, device):
        self._gps_device = device
        self._gps_start_timestamp = None

    def get_gps_start_timestamp(self):
        return self._gps_start_timestamp

    # === Properties ===

    _ALIGN4 = ('SUBARRAY_HPOS', 'SUBARRAY_VPOS', 'SUBARRAY_HSIZE', 'SUBARRAY_VSIZE')

    def set_property(self, prop_name: str, value: float) -> bool:
        if not self.is_connected and self._sky is None:
            # Allow defaults to be staged before connect
            pass
        try:
            value = float(value)
        except (TypeError, ValueError):
            logger.error(f"[SIM] Invalid value for {prop_name}: {value!r}")
            return False

        if prop_name in _ENUM_TEXT and value not in _ENUM_TEXT[prop_name]:
            logger.error(f"[SIM] Invalid value {value} for {prop_name}")
            return False

        with self._props_lock:
            if prop_name == 'EXPOSURE_TIME':
                value = min(max(value, 1e-5), 600.0)
                self._current_exposure = value
            elif prop_name in self._ALIGN4:
                value = float(int(value) // 4 * 4)
                limit = self.sensor_width if 'H' in prop_name[9] else self.sensor_height
                if prop_name.endswith('SIZE'):
                    value = min(max(value, 4.0), float(limit))
                else:
                    value = min(max(value, 0.0), float(limit - 4))
            elif prop_name.startswith('OUTPUT_TRIG') or prop_name.startswith('TRIGGER'):
                pass
            elif prop_name not in self._props and prop_name not in _DEFAULT_PROPS:
                # Unknown but harmless (e.g. TIME_STAMP_PRODUCER variants)
                logger.debug(f"[SIM] Storing unknown property {prop_name}={value}")
            self._props[prop_name] = value
            # Keep subarray geometry consistent
            if prop_name in self._ALIGN4 or prop_name == 'SUBARRAY_MODE':
                hp, hs = self._props['SUBARRAY_HPOS'], self._props['SUBARRAY_HSIZE']
                vp, vs = self._props['SUBARRAY_VPOS'], self._props['SUBARRAY_VSIZE']
                if hp + hs > self.sensor_width:
                    self._props['SUBARRAY_HSIZE'] = float(self.sensor_width - hp)
                if vp + vs > self.sensor_height:
                    self._props['SUBARRAY_VSIZE'] = float(self.sensor_height - vp)
        return True

    def get_property(self, prop_name: str) -> Optional[float]:
        if prop_name == 'SENSOR_TEMPERATURE':
            return -20.0 + 0.15 * math.sin(time.time() / 30.0)
        if prop_name == 'INTERNAL_FRAME_RATE':
            return 1.0 / self._frame_period()
        if prop_name == 'IMAGE_WIDTH':
            return float(self._image_shape()[1])
        if prop_name == 'IMAGE_HEIGHT':
            return float(self._image_shape()[0])
        if prop_name == 'IMAGE_FRAMEBYTES':
            h, w = self._image_shape()
            return float(h * w * 2)
        with self._props_lock:
            return self._props.get(prop_name)

    def _image_shape(self):
        with self._props_lock:
            p = self._props
            sub = p['SUBARRAY_MODE'] == 2.0
            h = int(p['SUBARRAY_VSIZE']) if sub else self.sensor_height
            w = int(p['SUBARRAY_HSIZE']) if sub else self.sensor_width
            b = int(p['BINNING'])
        return h // b, w // b

    def set_exposure(self, seconds: float) -> bool:
        return self.set_property('EXPOSURE_TIME', seconds)

    def get_exposure(self) -> Optional[float]:
        return self.get_property('EXPOSURE_TIME')

    def set_binning(self, factor: int) -> bool:
        if factor not in (1, 2, 4):
            return False
        return self.set_property('BINNING', float(factor))

    def set_trigger_source(self, source: str) -> bool:
        m = {'internal': 1.0, 'external': 2.0, 'software': 3.0}
        if source.lower() not in m:
            return False
        return self.set_property('TRIGGER_SOURCE', m[source.lower()])

    def get_all_params(self) -> Dict[str, Any]:
        """DCAM-style parameter dict: names with spaces, enum values as text."""
        with self._props_lock:
            props = dict(self._props)
        out: Dict[str, Any] = {}
        for name, value in props.items():
            text = _ENUM_TEXT.get(name, {}).get(value)
            out[name.replace('_', ' ')] = text if text is not None else value
        h, w = self._image_shape()
        out['IMAGE WIDTH'] = float(w)
        out['IMAGE HEIGHT'] = float(h)
        out['IMAGE FRAMEBYTES'] = float(h * w * 2)
        out['IMAGE ROWBYTES'] = float(w * 2)
        out['INTERNAL FRAME RATE'] = round(1.0 / self._frame_period(), 4)
        out['INTERNAL FRAME INTERVAL'] = round(self._frame_period(), 6)
        out['TIMING READOUT TIME'] = READOUT_TIME_S
        out['SENSOR TEMPERATURE'] = round(self.get_property('SENSOR_TEMPERATURE'), 2)
        out['SENSOR COOLER STATUS'] = 'READY'
        out['IMAGE DETECTOR PIXEL NUM HORZ'] = float(self.sensor_width)
        out['IMAGE DETECTOR PIXEL NUM VERT'] = float(self.sensor_height)
        out['CAMERA MODEL'] = 'SIMULATED qCMOS'
        return out

    def get_frame_rate(self) -> float:
        return self._fps

    # === Callbacks ===

    def on_frame(self, callback: Callable[[np.ndarray, float, int], None]):
        with self._callback_lock:
            if callback not in self._frame_callbacks:
                self._frame_callbacks.append(callback)

    def remove_frame_callback(self, callback: Callable):
        with self._callback_lock:
            if callback in self._frame_callbacks:
                self._frame_callbacks.remove(callback)

    # === Single capture ===

    def capture_single(self, timeout_ms: int = 30000) -> Optional[np.ndarray]:
        if not self.is_connected:
            logger.error("[SIM] Camera not connected")
            return None
        if self._capturing:
            logger.error("[SIM] Cannot capture single while streaming")
            return None
        exp = float(self._props.get('EXPOSURE_TIME', 1.0))
        time.sleep(min(exp, 30.0) + READOUT_TIME_S)
        return self._render_frame()

    def save_fits(self, data: np.ndarray, filepath: str, header_extra: dict = None,
                  object_name: str = None):
        from astropy.io import fits
        import warnings
        hdu = fits.PrimaryHDU(data=data)
        hdu.header['DATE-OBS'] = datetime.utcnow().isoformat()
        hdu.header['INSTRUME'] = 'Cerberus-SIM'
        if self._current_exposure is not None:
            hdu.header['EXPTIME'] = (self._current_exposure, 'Exposure time in seconds')
        if object_name:
            hdu.header['OBJECT'] = object_name
        if header_extra:
            for key, value in header_extra.items():
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    try:
                        hdu.header[key] = value
                    except Exception:
                        pass
        hdu.writeto(filepath, overwrite=True)

    # === Helpers ===

    def _apply_defaults(self):
        defaults = dict(_DEFAULT_PROPS)
        defaults.update(self._settings)
        for prop, value in defaults.items():
            self.set_property(prop, value)
