"""
Hardware Module

Provides interfaces to all hardware components:
- Camera (Hamamatsu qCMOS via DCAM)
- Telescope (P200 via TCS)
- Filter wheel
- GPS timing (Meinberg PCI card)
"""

from .camera import CameraController
from .telescope import TelescopeController
from .gps_timing import GPSTimingDevice, GPSTimestamp

__all__ = ['CameraController', 'TelescopeController', 'GPSTimingDevice', 'GPSTimestamp']
