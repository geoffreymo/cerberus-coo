"""Simulated Meinberg GPS timing device (drop-in for hardware.gps_timing.GPSTimingDevice)."""

import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    from ..hardware.gps_timing import GPSTimestamp
except Exception:  # pragma: no cover - fallback if ctypes module unavailable
    from dataclasses import dataclass

    @dataclass
    class GPSTimestamp:
        unix_seconds: float
        isot: str
        signal: int
        status: int


class SimGPSTimingDevice:
    """Returns a GPS-quality timestamp for 'now' on every request."""

    def __init__(self):
        self._connected = False
        self._cleared_at = 0.0

    def connect(self) -> bool:
        self._connected = True
        logger.info("[SIM] GPS timing device connected")
        return True

    @property
    def is_connected(self) -> bool:
        return self._connected

    def clear_buffer(self) -> bool:
        self._cleared_at = time.time()
        return True

    def get_buffer_count(self) -> int:
        return 1 if self._connected else 0

    def get_buffer_capacity(self) -> int:
        return 128

    def get_timestamp(self, skip_invalid: bool = True) -> Optional[GPSTimestamp]:
        if not self._connected:
            return None
        t = time.time()
        isot = datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')
        return GPSTimestamp(unix_seconds=t, isot=isot, signal=1, status=0)

    def get_all_timestamps(self) -> List[GPSTimestamp]:
        ts = self.get_timestamp()
        return [ts] if ts else []

    def disconnect(self):
        self._connected = False
