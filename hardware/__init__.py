"""
Hardware Module

Provides interfaces to all hardware components:
- Camera (Hamamatsu qCMOS via DCAM)
- Telescope (P200 via TCS)
- Filter wheel
"""

from .camera import CameraController
from .telescope import TelescopeController

__all__ = ['CameraController', 'TelescopeController']
