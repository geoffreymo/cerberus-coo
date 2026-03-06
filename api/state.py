# api/state.py
"""System state management for Cerberus API."""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from enum import Enum


class ConnectionState(Enum):
    """Device connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class CameraState:
    """
    State for a single camera.

    Each camera has its own independent state for streaming, saving, etc.
    """
    # Camera identification
    index: int = 0
    camera_id: str = ""

    # Connection and streaming
    connected: bool = False
    streaming: bool = False

    # Camera parameters
    exposure: Optional[float] = None
    temperature: Optional[float] = None
    frame_rate: Optional[float] = None
    params: Dict[str, Any] = field(default_factory=dict)

    # Frame counters
    frames_captured: int = 0
    frames_saved: int = 0
    frames_dropped: int = 0
    cubes_saved: int = 0

    # Acquisition state
    is_saving: bool = False
    save_object_name: Optional[str] = None
    save_output_dir: Optional[str] = None


@dataclass
class SystemState:
    """
    Current state of the Cerberus system.

    This dataclass holds the current state of all system components.
    It's updated by the API and can be used by GUI or scripts to
    monitor system status.
    """

    # Per-camera state (keyed by camera index)
    cameras: Dict[int, CameraState] = field(default_factory=dict)

    # Telescope state (shared across all cameras)
    telescope_connected: bool = False
    telescope_focus: Optional[float] = None
    telescope_ra: Optional[str] = None
    telescope_dec: Optional[str] = None
    telescope_ha: Optional[str] = None
    telescope_lst: Optional[str] = None
    telescope_airmass: Optional[float] = None
    telescope_utc: Optional[str] = None
    telescope_utc_day: Optional[str] = None
    telescope_utc_time: Optional[str] = None

    # Telescope status (from get_status)
    telescope_tube_length_mm: Optional[float] = None
    telescope_offset_ra_arcsec: Optional[float] = None
    telescope_offset_dec_arcsec: Optional[float] = None
    telescope_rate_ra_arcsec_hr: Optional[float] = None
    telescope_rate_dec_arcsec_hr: Optional[float] = None
    telescope_cass_ring_angle: Optional[float] = None
    telescope_id: Optional[str] = None

    # Filter wheel state (shared across all cameras)
    filterwheel_connected: bool = False
    current_filter: Optional[str] = None
    available_filters: list = field(default_factory=list)

    # Focus loop state
    focus_loop_running: bool = False
    focus_loop_progress: int = 0
    focus_loop_total_steps: int = 0

    # Error tracking
    last_error: Optional[str] = None
    errors: list = field(default_factory=list)

    # --- Convenience properties for backward compatibility ---

    def get_camera(self, index: int) -> CameraState:
        """Get camera state by index, creating if needed."""
        if index not in self.cameras:
            self.cameras[index] = CameraState(index=index)
        return self.cameras[index]

    @property
    def camera_connected(self) -> bool:
        """True if any camera is connected (backward compat)."""
        return any(cam.connected for cam in self.cameras.values())

    @property
    def camera_streaming(self) -> bool:
        """True if any camera is streaming (backward compat)."""
        return any(cam.streaming for cam in self.cameras.values())

    @property
    def is_saving(self) -> bool:
        """True if any camera is saving (backward compat)."""
        return any(cam.is_saving for cam in self.cameras.values())

    @property
    def camera_exposure(self) -> Optional[float]:
        """Exposure of first connected camera (backward compat)."""
        for cam in self.cameras.values():
            if cam.connected and cam.exposure is not None:
                return cam.exposure
        return None

    @property
    def camera_temperature(self) -> Optional[float]:
        """Temperature of first connected camera (backward compat)."""
        for cam in self.cameras.values():
            if cam.connected and cam.temperature is not None:
                return cam.temperature
        return None

    @property
    def camera_frame_rate(self) -> Optional[float]:
        """Frame rate of first connected camera (backward compat)."""
        for cam in self.cameras.values():
            if cam.connected and cam.frame_rate is not None:
                return cam.frame_rate
        return None

    @property
    def camera_frames_captured(self) -> int:
        """Total frames captured across all cameras (backward compat)."""
        return sum(cam.frames_captured for cam in self.cameras.values())

    @property
    def frames_saved(self) -> int:
        """Total frames saved across all cameras (backward compat)."""
        return sum(cam.frames_saved for cam in self.cameras.values())

    @property
    def frames_dropped(self) -> int:
        """Total frames dropped across all cameras (backward compat)."""
        return sum(cam.frames_dropped for cam in self.cameras.values())

    @property
    def cubes_saved(self) -> int:
        """Total cubes saved across all cameras (backward compat)."""
        return sum(cam.cubes_saved for cam in self.cameras.values())

    @property
    def camera_params(self) -> Dict[str, Any]:
        """Params of first connected camera (backward compat)."""
        for cam in self.cameras.values():
            if cam.connected:
                return cam.params
        return {}

    @property
    def save_object_name(self) -> Optional[str]:
        """Object name from first saving camera (backward compat)."""
        for cam in self.cameras.values():
            if cam.is_saving and cam.save_object_name:
                return cam.save_object_name
        return None

    @property
    def save_output_dir(self) -> Optional[str]:
        """Output dir from first saving camera (backward compat)."""
        for cam in self.cameras.values():
            if cam.is_saving and cam.save_output_dir:
                return cam.save_output_dir
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary."""
        cameras_dict = {}
        for idx, cam in self.cameras.items():
            cameras_dict[idx] = {
                'index': cam.index,
                'camera_id': cam.camera_id,
                'connected': cam.connected,
                'streaming': cam.streaming,
                'exposure': cam.exposure,
                'temperature': cam.temperature,
                'frame_rate': cam.frame_rate,
                'frames_captured': cam.frames_captured,
                'is_saving': cam.is_saving,
                'frames_saved': cam.frames_saved,
                'frames_dropped': cam.frames_dropped,
                'cubes_saved': cam.cubes_saved,
            }

        return {
            'cameras': cameras_dict,
            'telescope': {
                'connected': self.telescope_connected,
                'focus': self.telescope_focus,
                'ra': self.telescope_ra,
                'dec': self.telescope_dec,
                'ha': self.telescope_ha,
                'lst': self.telescope_lst,
                'airmass': self.telescope_airmass,
                'utc': self.telescope_utc,
                'utc_day': self.telescope_utc_day,
                'utc_time': self.telescope_utc_time,
                'tube_length_mm': self.telescope_tube_length_mm,
                'offset_ra_arcsec': self.telescope_offset_ra_arcsec,
                'offset_dec_arcsec': self.telescope_offset_dec_arcsec,
                'rate_ra_arcsec_hr': self.telescope_rate_ra_arcsec_hr,
                'rate_dec_arcsec_hr': self.telescope_rate_dec_arcsec_hr,
                'cass_ring_angle': self.telescope_cass_ring_angle,
                'telescope_id': self.telescope_id,
            },
            'filterwheel': {
                'connected': self.filterwheel_connected,
                'current_filter': self.current_filter,
                'available_filters': self.available_filters,
            },
            'focus_loop': {
                'running': self.focus_loop_running,
                'progress': self.focus_loop_progress,
                'total_steps': self.focus_loop_total_steps,
            },
            'errors': {
                'last': self.last_error,
                'all': self.errors,
            }
        }

    def add_error(self, error: str):
        """Add an error to the error list."""
        self.last_error = error
        self.errors.append(error)
        # Keep only last 100 errors
        if len(self.errors) > 100:
            self.errors = self.errors[-100:]

    def clear_errors(self):
        """Clear all errors."""
        self.last_error = None
        self.errors = []
