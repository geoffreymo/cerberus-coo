# config.py
"""
Configuration loader for Cerberus system.

Loads settings from config.json and provides typed access to all
configuration parameters.
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Default config file location (same directory as this module)
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


@dataclass
class TelescopeConfig:
    """Telescope/TCS configuration."""
    host: str = "198.202.125.194"
    port: int = 49200
    timeout_seconds: float = 30.0
    focus_min_mm: float = 1.0
    focus_max_mm: float = 74.0
    auto_connect: bool = True


@dataclass
class CameraConfig:
    """Camera configuration."""
    buffer_size: int = 100
    defaults: Dict[str, float] = field(default_factory=dict)
    capture_timeout_ms: int = 30000
    align_to_second_offset: float = 0.10


@dataclass
class FilterWheelConfig:
    """Filter wheel configuration."""
    library_path: str = "libEFWFilter.so.1.7"
    filters: Dict[str, str] = field(default_factory=dict)  # position -> name
    focus_positions_mm: Dict[str, Optional[float]] = field(default_factory=dict)  # name -> absolute focus position (None if not calibrated)


@dataclass
class FocusLoopConfig:
    """Focus loop configuration."""
    start_position_mm: float = 30.0
    end_position_mm: float = 45.0
    step_size_mm: float = 2.5
    exposure_time_seconds: float = 5.0
    settle_time_seconds: float = 2.0
    max_fwhm_arcsec: float = 5.0
    auto_apply_best: bool = True


@dataclass
class InstrumentConfig:
    """Instrument-specific configuration."""
    plate_scale_arcsec_per_pixel: float = 0.051
    saturation_level_adu: int = 60000
    min_fwhm_pixels: int = 5
    fwhm_box_size_pixels: int = 80
    timestamp_rollover_threshold: int = 4000
    framestamp_rollover_threshold: int = 60000


@dataclass
class AcquisitionConfig:
    """Acquisition/writer configuration."""
    max_queue_size: int = 10000
    max_pending_writes: int = 6
    frames_per_cube: int = 100
    thread_pool_workers: int = 8
    backpressure_threshold: float = 0.9


@dataclass
class PathsConfig:
    """Path configuration."""
    default_output_dir: str = "/data/cerberus"
    focus_output_dir: str = "/tmp/cerberus_focus"
    log_dir: str = "/data/cerberus"


@dataclass
class GUIConfig:
    """GUI configuration."""
    status_update_interval_ms: int = 1000
    default_object_name: str = "Object"
    default_focus_display_mm: float = 35.0


@dataclass
class GuidingConfig:
    """Autoguiding configuration."""
    averaging_window_seconds: float = 15.0  # Window for averaging positions
    correction_threshold_arcsec: float = 0.1  # Min drift before correcting
    max_correction_arcsec: float = 5.0  # Safety limit per correction
    correction_interval_seconds: float = 5.0  # Time between corrections
    guide_gain: float = 0.8  # Fraction of error to correct (0-1)
    # Coordinate mapping (depends on camera orientation)
    x_to_ra_sign: int = -1  # +1 or -1: how +X pixel maps to RA
    y_to_dec_sign: int = -1  # +1 or -1: how +Y pixel maps to Dec


@dataclass
class CerberusConfig:
    """Complete Cerberus system configuration."""
    telescope: TelescopeConfig = field(default_factory=TelescopeConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    filterwheel: FilterWheelConfig = field(default_factory=FilterWheelConfig)
    focusloop: FocusLoopConfig = field(default_factory=FocusLoopConfig)
    instrument: InstrumentConfig = field(default_factory=InstrumentConfig)
    acquisition: AcquisitionConfig = field(default_factory=AcquisitionConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    gui: GUIConfig = field(default_factory=GUIConfig)
    guiding: GuidingConfig = field(default_factory=GuidingConfig)

    def get_filter_focus_position(self, filter_name: str) -> Optional[float]:
        """
        Get absolute focus position for a filter.

        Args:
            filter_name: Filter name

        Returns:
            Focus position in mm, or None if not calibrated
        """
        return self.filterwheel.focus_positions_mm.get(filter_name)

    def set_filter_focus_position(self, filter_name: str, position_mm: float):
        """
        Set absolute focus position for a filter.

        Args:
            filter_name: Filter name
            position_mm: Focus position in mm
        """
        self.filterwheel.focus_positions_mm[filter_name] = position_mm

    def get_filter_name(self, position: int) -> Optional[str]:
        """
        Get filter name for a position.

        Args:
            position: Filter wheel position (0-indexed)

        Returns:
            Filter name or None if not configured
        """
        return self.filterwheel.filters.get(str(position))


# Global config instance (loaded on first access)
_config: Optional[CerberusConfig] = None


def load_config(config_path: str = None) -> CerberusConfig:
    """
    Load configuration from JSON file.

    Args:
        config_path: Path to config file (uses default if None)

    Returns:
        CerberusConfig instance
    """
    global _config

    path = config_path or DEFAULT_CONFIG_PATH

    if not os.path.exists(path):
        logger.warning(f"Config file not found: {path}, using defaults")
        _config = CerberusConfig()
        return _config

    try:
        with open(path, 'r') as f:
            data = json.load(f)

        logger.info(f"Loading config from: {path}")

        # Build config from JSON
        telescope = TelescopeConfig(**data.get('telescope', {}))

        camera_data = data.get('camera', {})
        camera = CameraConfig(
            buffer_size=camera_data.get('buffer_size', 100),
            defaults=camera_data.get('defaults', {}),
            capture_timeout_ms=camera_data.get('capture_timeout_ms', 30000),
            align_to_second_offset=camera_data.get('align_to_second_offset', 0.10)
        )

        filterwheel = FilterWheelConfig(**data.get('filterwheel', {}))
        focusloop = FocusLoopConfig(**data.get('focusloop', {}))
        instrument = InstrumentConfig(**data.get('instrument', {}))
        acquisition = AcquisitionConfig(**data.get('acquisition', {}))
        paths = PathsConfig(**data.get('paths', {}))
        gui = GUIConfig(**data.get('gui', {}))
        guiding = GuidingConfig(**data.get('guiding', {}))

        _config = CerberusConfig(
            telescope=telescope,
            camera=camera,
            filterwheel=filterwheel,
            focusloop=focusloop,
            instrument=instrument,
            acquisition=acquisition,
            paths=paths,
            gui=gui,
            guiding=guiding
        )

        return _config

    except Exception as e:
        logger.error(f"Error loading config: {e}")
        _config = CerberusConfig()
        return _config


def get_config() -> CerberusConfig:
    """
    Get the current configuration.

    Loads from default path if not already loaded.

    Returns:
        CerberusConfig instance
    """
    global _config
    if _config is None:
        load_config()
    return _config


def reload_config(config_path: str = None) -> CerberusConfig:
    """
    Reload configuration from file.

    Args:
        config_path: Path to config file (uses default if None)

    Returns:
        CerberusConfig instance
    """
    global _config
    _config = None
    return load_config(config_path)
