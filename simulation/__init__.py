"""
Hardware simulation for Cerberus.

Provides drop-in replacements for the camera controller, telescope controller,
filter wheel and GPS timing device so the full application (API + GUI + focus
loop + FITS writing) can be exercised with no hardware attached.

    from cerberus_coo.simulation import make_simulated_api
    api = make_simulated_api(n_cameras=2)

The simulated camera renders a synthetic star field whose FWHM depends on the
simulated telescope focus, drifts slowly (so guiding can be tested), contains a
variable star (so photometry can be tested) and honours subarray/binning.
"""

from .world import SimWorld
from .sim_camera import SimCameraController

# Default location for simulated captures / focus images / config copies
SIM_DATA_DIR = "/tmp/cerberus_sim"
from .sim_telescope import SimTelescopeController
from .sim_filterwheel import SimFilterWheel
from .sim_gps import SimGPSTimingDevice


def make_simulated_api(n_cameras: int = 2, cameras=None, world: SimWorld = None,
                       sensor_size=None):
    """
    Build a CerberusAPI wired to simulated hardware.

    Args:
        n_cameras: Number of simulated cameras (ignored if `cameras` given)
        cameras: Optional explicit list of (index, camera_id) tuples
        world: Optional shared SimWorld (created if None)
        sensor_size: Optional (width, height) override for the simulated sensor
    """
    from ..api import CerberusAPI

    if world is None:
        world = SimWorld()
    if cameras is None:
        cameras = [(i, f"SIM{i + 1}") for i in range(max(1, n_cameras))]

    api = CerberusAPI(
        cameras=cameras,
        camera_factory=lambda: SimCameraController(world, sensor_size=sensor_size),
        telescope_factory=lambda **kw: SimTelescopeController(world),
        filterwheel_factory=lambda **kw: SimFilterWheel(kw.get('filters'), world=world),
        gps_factory=lambda: SimGPSTimingDevice(),
    )
    api.simulated = True
    api.sim_world = world
    # Never overwrite the real config.json from a simulated session
    import os
    api.config_save_path = os.path.join(SIM_DATA_DIR, "config.sim.json")
    return api


__all__ = [
    'SimWorld', 'SimCameraController', 'SimTelescopeController',
    'SimFilterWheel', 'SimGPSTimingDevice', 'make_simulated_api',
]
