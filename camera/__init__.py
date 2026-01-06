"""
Cerberus Camera Module

Provides camera interface for automated observations.
Now consolidated - uses the main CameraController from hardware.camera.
"""

from ..hardware.camera.controller import CameraController

# Alias for backwards compatibility with focus loop and scripts
CerberusCamera = CameraController

__all__ = ['CerberusCamera', 'CameraController']
