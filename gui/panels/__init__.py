"""
GUI Panels

Modular panel components for the Cerberus GUI.
"""

from .camera_controls import CameraControlsPanel
from .camera_settings import CameraSettingsPanel
from .subarray_panel import SubarrayPanel
from .image_display import ImageDisplayPanel
from .output_controls import OutputControlsPanel
from .telescope_panel import TelescopePanel
from .filter_panel import FilterPanel
from .focus_panel import FocusPanel
from .status_bar import StatusBar

__all__ = [
    'CameraControlsPanel',
    'CameraSettingsPanel',
    'SubarrayPanel',
    'ImageDisplayPanel',
    'OutputControlsPanel',
    'TelescopePanel',
    'FilterPanel',
    'FocusPanel',
    'StatusBar',
]
