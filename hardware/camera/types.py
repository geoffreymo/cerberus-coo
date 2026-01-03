# hardware/camera/types.py
"""Shared types and enums for camera control."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TriggerSource(Enum):
    """Camera trigger source options."""
    INTERNAL = 1.0
    EXTERNAL = 2.0
    SOFTWARE = 3.0


class TriggerMode(Enum):
    """Camera trigger mode options."""
    NORMAL = 1.0
    START = 6.0


class SensorMode(Enum):
    """Camera sensor mode options."""
    AREA = 1.0
    LINE = 2.0
    TDI = 3.0


@dataclass
class FrameData:
    """Container for frame data with timing information."""
    data: 'np.ndarray'  # The image data
    timestamp: float     # Camera timestamp in seconds (relative)
    framestamp: int      # Frame counter from camera
    frame_index: int     # Sequential frame index in this capture session


@dataclass
class CameraStatus:
    """Current camera status."""
    is_connected: bool = False
    is_streaming: bool = False
    temperature: Optional[float] = None
    exposure_time: Optional[float] = None
    frame_rate: Optional[float] = None
    frames_captured: int = 0
