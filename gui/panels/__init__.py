"""
GUI Panels

Modular panel components for the Cerberus GUI.
"""

from .camera_controls import CameraControlsPanel
from .image_display import ImageDisplayPanel
from .output_controls import OutputControlsPanel
from .telescope_panel import TelescopePanel
from .filter_panel import FilterPanel
from .status_bar import StatusBar

__all__ = [
    'CameraControlsPanel',
    'ImageDisplayPanel',
    'OutputControlsPanel',
    'TelescopePanel',
    'FilterPanel',
    'StatusBar',
]
