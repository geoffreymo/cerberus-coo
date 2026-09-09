"""
End-to-end tests of CerberusAPI against the simulated hardware.

Run:  pytest tests/test_simulation_api.py -v
"""

import glob
import os
import time

import numpy as np
import pytest
from astropy.io import fits


def _wait(pred, timeout=10.0, step=0.05):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(step)
    return False


def test_connect_everything(sim_api):
    api = sim_api
    assert api.connect_camera(0)
    assert api.connect_camera(1)
    assert api.connect_telescope()
    assert api.connect_filterwheel()
    assert api.connect_gps()
    st = api.state
    assert st.get_camera(0).connected and st.get_camera(1).connected
    assert st.telescope_connected and st.filterwheel_connected and st.gps_connected
    assert st.available_filters == ['clear', 'u', 'g', 'r', 'i', 'z', 'Ha', 'OIII']
    params = api.get_camera_params(0)
    assert params['READOUT SPEED'] == 'ULTRA QUIET'
    assert params['TRIGGER SOURCE'] == 'EXTERNAL'
    assert params['IMAGE WIDTH'] == 1024.0


def test_stream_and_save_fits(sim_api, _tmp_data_dir):
    api = sim_api
    api.connect_camera(0)
    api.connect_telescope()
    api.connect_filterwheel()
    api.connect_gps()
    api.set_exposure(0.05, camera_index=0)
    frames = []
    api.on_frame(lambda f, ts, fs: frames.append((f.shape, ts, fs)))
    assert api.start_streaming(camera_index=0)
    assert api.start_saving("TestObj", _tmp_data_dir, frames_per_cube=10, comment="unit test", camera_index=0)
    assert not api.start_saving("TestObj", _tmp_data_dir, camera_index=0), "double start_saving must be refused"
    assert _wait(lambda: api.state.get_camera(0).frames_captured >= 25, 10)
    api.update_status()
    cam = api.state.get_camera(0)
    assert cam.frame_rate > 5
    assert cam.temperature is not None
    api.stop_streaming(camera_index=0)
    assert _wait(lambda: not api.state.get_camera(0).is_saving, 10)
    cam = api.state.get_camera(0)
    assert cam.frames_saved >= 20 and cam.cubes_saved >= 2 and cam.frames_dropped == 0
    files = sorted(glob.glob(os.path.join(_tmp_data_dir, "captures_*", "SIM1", "*.fits")))
    assert files, "no FITS cubes written"
    with fits.open(files[0]) as hdul:
        assert [h.name for h in hdul] == ['PRIMARY', 'DATA_CUBE', 'TIMESTAMPS']
        assert hdul[1].data.shape == (10, 768, 1024)
        assert hdul[1].data.dtype == np.uint16
        hdr = hdul[0].header
        assert hdr['OBJECT'] == 'TestObj'
        assert hdr['CAMERAID'] == 'SIM1'
        assert hdr['FILTER'] == 'clear'
        assert 'TELRA' in hdr and 'TELFOCUS' in hdr and 'GPSSTART' in hdr
        assert 'GPSTIME' in hdul[2].columns.names
        fs = hdul[2].data['FRAMESTAMP']
        assert np.all(np.diff(fs) == 1), "framestamps must be consecutive"
    # frames delivered to callbacks have the sensor shape
    assert frames and frames[0][0] == (768, 1024)


def test_subarray_and_binning(sim_api):
    api = sim_api
    api.connect_camera(0)
    assert api.set_camera_property("SUBARRAY_MODE", 2.0, camera_index=0)
    assert api.set_camera_property("SUBARRAY_HSIZE", 250.0, camera_index=0)   # rounded to 248
    assert api.set_camera_property("SUBARRAY_VSIZE", 128.0, camera_index=0)
    assert api.set_camera_property("SUBARRAY_HPOS", 100.0, camera_index=0)
    p = api.get_camera_params(0)
    assert p['SUBARRAY MODE'] == 'ON' and p['SUBARRAY HSIZE'] == 248.0 and p['IMAGE WIDTH'] == 248.0
    frame = api.capture_single_to_fits.__self__.cameras[0].capture_single()
    assert frame.shape == (128, 248)
    assert api.set_camera_property("BINNING", 2.0, camera_index=0)
    frame = api.cameras[0].capture_single()
    assert frame.shape == (64, 124)
    api.set_camera_property("BINNING", 1.0, camera_index=0)
    api.set_camera_property("SUBARRAY_MODE", 1.0, camera_index=0)
    assert api.cameras[0].capture_single().shape == (768, 1024)


def test_filter_change_applies_focus_preset(sim_api):
    api = sim_api
    api.connect_telescope()
    api.connect_filterwheel()
    from cerberus_coo.config import get_config
    target = get_config().get_filter_focus_position('r')
    assert api.set_filter('r')
    assert api.state.current_filter == 'r'
    assert _wait(lambda: abs(api.get_focus() - target) < 0.05, 20)
    # case-insensitive request resolves to the wheel's canonical name
    assert api.set_filter('ha', apply_focus=False)
    assert api.state.current_filter == 'Ha'


def test_telescope_moves(sim_api):
    api = sim_api
    api.connect_telescope()
    assert api.set_focus(30.0)
    assert _wait(lambda: abs(api.get_focus() - 30.0) < 0.05, 20)
    assert api.offset_focus(-0.5)
    assert _wait(lambda: abs(api.get_focus() - 29.5) < 0.05, 20)
    assert not api.set_focus(500.0), "out-of-range focus must be rejected"
    assert api.move_offset(1.5, -2.0)
    st = api.telescope.get_status()
    assert st.offset_ra_arcsec == 1.5 and st.offset_dec_arcsec == -2.0
    pos = api.telescope.get_position()
    assert pos.ra.startswith("12:34:5") and pos.airmass >= 1.0


def test_focus_loop_finds_optimum(sim_api, _tmp_data_dir):
    """Real FocusLoop + FocusAnalyzer (SExtractor) on simulated star fields."""
    import shutil
    from cerberus_coo.focusloop import FocusLoopConfig
    from cerberus_coo.focusloop.focus_analyzer import FocusAnalyzer
    try:
        FocusAnalyzer._sextractor_binary()
    except FileNotFoundError:
        pytest.skip("SExtractor not installed")
    api = sim_api
    api.connect_camera(0)
    api.connect_telescope()
    api.sim_world.optimal_focus_mm = 26.5
    cfg = FocusLoopConfig(start_position=25.0, end_position=28.0, step_size=0.5, exposure_time=0.05,
                          output_dir=os.path.join(_tmp_data_dir, "focus"), settle_time=0.05)
    results = api.run_focus_loop(config=cfg, camera_index=0)
    assert results and None in results
    res = results[None]
    assert res.success, res.error_message
    assert abs(res.best_focus - 26.5) < 0.3
    assert len(res.measurements) == 7
    assert _wait(lambda: abs(api.get_focus() - res.best_focus) < 0.05, 20)
    # config written to the sim path, never the package config.json
    assert os.path.exists(api.config_save_path)
    assert not api.state.focus_loop_running


def test_status_callbacks_fire_off_lock(sim_api):
    """A status callback may itself call the API without deadlocking."""
    api = sim_api
    seen = []

    def cb(state):
        seen.append(state.telescope_connected)
        api.get_cameras()          # re-entrant API use inside a callback
        api.remove_status_callback(cb)   # and un-registering from inside

    api.on_status_change(cb)
    assert api.connect_telescope()
    assert seen and seen[0] is True
    assert cb not in api._status_callbacks
