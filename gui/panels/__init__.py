"""
GUI Panels

Modular panel components for the Cerberus GUI.
"""

from .camera_controls import CameraControlsPanel
from .subarray_panel import SubarrayPanel
from .image_display import ImageDisplayPanel
from .output_controls import OutputControlsPanel
from .filter_panel import FilterPanel
from .focus_panel import FocusPanel
from .status_bar import StatusBar

__all__ = [
    'CameraControlsPanel',
    'SubarrayPanel',
    'ImageDisplayPanel',
    'OutputControlsPanel',
    'FilterPanel',
    'FocusPanel',
    'StatusBar',
]
