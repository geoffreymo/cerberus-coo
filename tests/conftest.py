"""Shared pytest fixtures: simulated hardware, temporary data dir."""

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _tmp_data_dir(monkeypatch):
    """Point every test at a throw-away data directory and never touch config.json."""
    from cerberus_coo.config import get_config
    tmp = tempfile.mkdtemp(prefix="cerberus_test_")
    cfg = get_config()
    monkeypatch.setattr(cfg.paths, "default_output_dir", tmp)
    monkeypatch.setattr(cfg.paths, "focus_output_dir", os.path.join(tmp, "focus"))
    yield tmp


@pytest.fixture
def sim_api(_tmp_data_dir):
    from cerberus_coo.simulation import make_simulated_api
    api = make_simulated_api(n_cameras=2, sensor_size=(1024, 768))
    api.config_save_path = os.path.join(_tmp_data_dir, "config.sim.json")
    yield api
    try:
        with api:
            pass
    except Exception:
        pass
    api.disconnect_gps()
