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
class SystemState:
    """
    Current state of the Cerberus system.

    This dataclass holds the current state of all system components.
    It's updated by the API and can be used by GUI or scripts to
    monitor system status.
    """

    # Camera state
    camera_connected: bool = False
    camera_streaming: bool = False
    camera_exposure: Optional[float] = None
    camera_temperature: Optional[float] = None
    camera_frame_rate: Optional[float] = None
    camera_frames_captured: int = 0
    camera_params: Dict[str, Any] = field(default_factory=dict)

    # Telescope state (from get_position)
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

    # Filter wheel state
    filterwheel_connected: bool = False
    current_filter: Optional[str] = None
    available_filters: list = field(default_factory=list)

    # Acquisition state
    is_saving: bool = False
    save_object_name: Optional[str] = None
    save_output_dir: Optional[str] = None
    frames_saved: int = 0
    frames_dropped: int = 0
    cubes_saved: int = 0

    # Focus loop state
    focus_loop_running: bool = False
    focus_loop_progress: int = 0
    focus_loop_total_steps: int = 0

    # Error tracking
    last_error: Optional[str] = None
    errors: list = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary."""
        return {
            'camera': {
                'connected': self.camera_connected,
                'streaming': self.camera_streaming,
                'exposure': self.camera_exposure,
                'temperature': self.camera_temperature,
                'frame_rate': self.camera_frame_rate,
                'frames_captured': self.camera_frames_captured,
            },
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
            'acquisition': {
                'saving': self.is_saving,
                'object_name': self.save_object_name,
                'output_dir': self.save_output_dir,
                'frames_saved': self.frames_saved,
                'frames_dropped': self.frames_dropped,
                'cubes_saved': self.cubes_saved,
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
