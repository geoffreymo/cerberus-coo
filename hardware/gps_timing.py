# hardware/gps_timing.py
"""
GPS timing module for Meinberg PCI card.

Provides precision timestamps from User Capture (UCAP) buffer,
which records the GPS time of each incoming pulse with nanosecond precision.

The UCAP buffer is used to timestamp:
1. The READOUTEND pulse from the first frame (GPSSTART)
2. Each READOUTEND pulse from the camera (GPSTIME array)
"""

import ctypes
import threading
import logging
from ctypes import byref, c_int, c_uint32, c_void_p
from typing import Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class PCPS_HR_TIME(ctypes.Structure):
    """Meinberg high-resolution time structure."""
    _fields_ = [
        ("tstamp_sec", c_uint32),   # Unix seconds
        ("tstamp_frac", c_uint32),  # Fractional seconds (binary fraction of 2^32)
        ("signal", c_uint32),       # Signal status
        ("status", c_uint32)        # Status flags
    ]


class PCPS_UCAP_ENTRIES(ctypes.Structure):
    """UCAP buffer status."""
    _fields_ = [
        ("used", c_uint32),   # Number of entries in buffer
        ("max", c_uint32)     # Maximum buffer capacity
    ]


@dataclass
class GPSTimestamp:
    """GPS timestamp from UCAP buffer."""
    unix_seconds: float      # Unix timestamp with nanosecond precision
    isot: str               # ISO 8601 format string
    signal: int             # Signal status
    status: int             # Status flags


class GPSTimingDevice:
    """
    Interface to Meinberg GPS card UCAP buffer.

    The UCAP (User Capture) buffer records timestamps of external pulses
    with GPS precision. We use this to timestamp:
    1. The READOUTEND pulse from the first frame (GPSSTART)
    2. Each READOUTEND pulse from the camera (GPSTIME array)

    Usage:
        device = GPSTimingDevice()
        if device.connect():
            device.clear_buffer()
            # ... start camera capture ...
            while capturing:
                ts = device.get_timestamp()
                if ts:
                    print(f"Frame timestamp: {ts.isot}")
            device.disconnect()
    """

    # Library name for Meinberg driver
    LIBRARY_NAME = 'libmbgdevio.so'

    def __init__(self):
        self.libmbg = None
        self.device_handle = None
        self._lock = threading.Lock()
        self._connected = False

    def connect(self) -> bool:
        """
        Connect to Meinberg device.

        Returns:
            True if connection successful, False otherwise.
        """
        try:
            self.libmbg = ctypes.CDLL(self.LIBRARY_NAME)

            # Set up function prototypes
            self.libmbg.mbg_find_devices.restype = c_int

            self.libmbg.mbg_open_device.argtypes = [c_int]
            self.libmbg.mbg_open_device.restype = c_void_p

            self.libmbg.mbg_get_ucap_entries.argtypes = [
                c_void_p,
                ctypes.POINTER(PCPS_UCAP_ENTRIES)
            ]
            self.libmbg.mbg_get_ucap_entries.restype = c_int

            self.libmbg.mbg_get_ucap_event.argtypes = [
                c_void_p,
                ctypes.POINTER(PCPS_HR_TIME)
            ]
            self.libmbg.mbg_get_ucap_event.restype = c_int

            self.libmbg.mbg_clr_ucap_buff.argtypes = [c_void_p]
            self.libmbg.mbg_clr_ucap_buff.restype = c_int

            self.libmbg.mbg_close_device.argtypes = [c_void_p]
            self.libmbg.mbg_close_device.restype = c_int

            # Find devices
            num_devices = self.libmbg.mbg_find_devices()
            if num_devices == 0:
                logger.error("No Meinberg GPS device found")
                return False

            logger.info(f"Found {num_devices} Meinberg device(s)")

            # Open first device
            self.device_handle = self.libmbg.mbg_open_device(0)
            if not self.device_handle:
                logger.error("Failed to open Meinberg device")
                return False

            self._connected = True
            logger.info("GPS timing device connected")

            # Log buffer capacity
            entries = PCPS_UCAP_ENTRIES()
            rc = self.libmbg.mbg_get_ucap_entries(self.device_handle, byref(entries))
            if rc == 0:
                logger.info(f"UCAP buffer capacity: {entries.max} entries")

            return True

        except OSError as e:
            logger.warning(f"GPS device not available (library not found): {e}")
            return False
        except Exception as e:
            logger.error(f"GPS device connection failed: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        """Check if device is connected."""
        return self._connected

    def clear_buffer(self) -> bool:
        """
        Clear UCAP buffer.

        Call this before starting capture to ensure timestamps
        correspond to the current capture session.

        Returns:
            True if buffer cleared successfully.
        """
        with self._lock:
            if not self._connected:
                return False

            rc = self.libmbg.mbg_clr_ucap_buff(self.device_handle)
            if rc == 0:
                logger.debug("GPS UCAP buffer cleared")
                return True

            logger.warning(f"Failed to clear GPS buffer: rc={rc}")
            return False

    def get_buffer_count(self) -> int:
        """
        Get number of timestamps waiting in buffer.

        Returns:
            Number of unread timestamps in UCAP buffer.
        """
        with self._lock:
            if not self._connected:
                return 0

            entries = PCPS_UCAP_ENTRIES()
            rc = self.libmbg.mbg_get_ucap_entries(self.device_handle, byref(entries))
            if rc == 0:
                return entries.used
            return 0

    def get_buffer_capacity(self) -> int:
        """
        Get maximum buffer capacity.

        Returns:
            Maximum number of timestamps the buffer can hold.
        """
        with self._lock:
            if not self._connected:
                return 0

            entries = PCPS_UCAP_ENTRIES()
            rc = self.libmbg.mbg_get_ucap_entries(self.device_handle, byref(entries))
            if rc == 0:
                return entries.max
            return 0

    def get_timestamp(self, skip_invalid: bool = True) -> Optional[GPSTimestamp]:
        """
        Get next timestamp from UCAP buffer (FIFO order).

        This is non-blocking and returns None if the buffer is empty.
        Call this for each frame to get the corresponding GPS timestamp.

        Args:
            skip_invalid: If True, skip timestamps with unix_time <= 0 or
                         timestamps before year 2000 (likely invalid GPS data).

        Returns:
            GPSTimestamp if available, None if buffer empty or invalid.
        """
        with self._lock:
            if not self._connected:
                return None

            # Check if buffer has entries
            entries = PCPS_UCAP_ENTRIES()
            rc = self.libmbg.mbg_get_ucap_entries(self.device_handle, byref(entries))
            if rc != 0 or entries.used == 0:
                return None

            # Get the timestamp
            ucap = PCPS_HR_TIME()
            rc = self.libmbg.mbg_get_ucap_event(self.device_handle, byref(ucap))
            if rc != 0:
                logger.warning(f"Failed to get UCAP event: rc={rc}")
                return None

            # Convert to float with nanosecond precision
            # tstamp_frac is a binary fraction where 2^32 = 1 second
            unix_time = ucap.tstamp_sec + ucap.tstamp_frac / (2**32)

            # Validate timestamp - year 2000 is unix time 946684800
            if skip_invalid and unix_time < 946684800:
                logger.warning(f"Invalid GPS timestamp: unix={unix_time}, "
                             f"signal={ucap.signal}, status={ucap.status}")
                return None

            # Convert to ISO format using astropy for precision
            try:
                from astropy.time import Time
                astro_time = Time(unix_time, format='unix', precision=9)
                isot = astro_time.isot
            except ImportError:
                # Fallback if astropy not available
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(unix_time, tz=timezone.utc)
                isot = dt.isoformat()

            return GPSTimestamp(
                unix_seconds=unix_time,
                isot=isot,
                signal=ucap.signal,
                status=ucap.status
            )

    def get_all_timestamps(self) -> List[GPSTimestamp]:
        """
        Get all available timestamps from buffer.

        Drains the entire UCAP buffer and returns all timestamps.
        Useful for catching up if processing fell behind.

        Returns:
            List of GPSTimestamp objects.
        """
        timestamps = []
        while True:
            ts = self.get_timestamp()
            if ts is None:
                break
            timestamps.append(ts)
        return timestamps

    def disconnect(self):
        """Close device connection."""
        with self._lock:
            if self._connected and self.device_handle:
                self.libmbg.mbg_close_device(self.device_handle)
                self._connected = False
                self.device_handle = None
                logger.info("GPS timing device disconnected")

    def __del__(self):
        """Ensure device is closed on deletion."""
        if self._connected:
            self.disconnect()


# Convenience function for testing
def test_gps_device():
    """Test GPS device connectivity and read timestamps."""
    import time
    from ctypes import byref

    device = GPSTimingDevice()

    print("Connecting to GPS device...")
    if not device.connect():
        print("Failed to connect to GPS device")
        return False

    print(f"Connected: {device.is_connected}")
    print(f"Buffer capacity: {device.get_buffer_capacity()}")
    print(f"Buffer count: {device.get_buffer_count()}")

    print("\nClearing buffer...")
    device.clear_buffer()

    print("Waiting for timestamps (press Ctrl+C to stop)...")
    print("Format: ISOT | unix_seconds | signal | status")
    print("-" * 70)

    try:
        poll_count = 0
        while True:
            # Debug: show raw entries struct every 10 polls
            entries = PCPS_UCAP_ENTRIES()
            rc = device.libmbg.mbg_get_ucap_entries(device.device_handle, byref(entries))

            poll_count += 1
            if poll_count % 10 == 0:
                print(f"  [poll {poll_count}] mbg_get_ucap_entries rc={rc}, used={entries.used}, max={entries.max}")

            if entries.used > 0:
                # Get raw timestamp (don't skip invalid) to see all data
                ts = device.get_timestamp(skip_invalid=False)
                if ts:
                    valid = "OK" if ts.unix_seconds > 946684800 else "INVALID"
                    print(f"  {ts.isot} | {ts.unix_seconds:.9f} | "
                          f"sig={ts.signal} | stat={ts.status} | {valid}")
                else:
                    print(f"  [entries.used={entries.used} but get_timestamp returned None]")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopped")

    device.disconnect()
    return True


if __name__ == "__main__":
    # Run standalone test (bypasses package imports)
    import sys
    import os
    # Remove parent from path to avoid package import issues
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    test_gps_device()
