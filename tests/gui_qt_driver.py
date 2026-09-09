"""
Scripted end-to-end session of the PyQt6 GUI against simulated hardware.

Exercises every user-facing feature (connect, exposure, streaming, saving,
live view, FWHM tracking, guiding, photometry, ROI drag, N-frames, filter
change, all secondary windows, focus loop, second camera, theme, shutdown)
and writes screenshots to the directory given as argv[1].

    QT_QPA_PLATFORM=offscreen python tests/gui_qt_driver.py /tmp/shots

Exit code 0 = all checks passed. Normally run via tests/test_gui_qt_sim.py.
"""
import logging, os, sys, time, glob
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
for noisy in ('cerberus_coo.simulation.sim_camera', 'matplotlib', 'PIL'):
    logging.getLogger(noisy).setLevel(logging.WARNING)

SHOTS = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots"
os.makedirs(SHOTS, exist_ok=True)
SIZE = (2048, 1152)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtTest import QTest
from cerberus_coo.config import get_config
from cerberus_coo.simulation import make_simulated_api, SIM_DATA_DIR
from cerberus_coo.gui_qt.theme import apply_theme
from cerberus_coo.gui_qt.app import CerberusQtGUI

cfg = get_config()
cfg.paths.default_output_dir = SIM_DATA_DIR
api = make_simulated_api(n_cameras=2, sensor_size=SIZE)
cameras = api.get_cameras()

app = QApplication(sys.argv)
apply_theme(app, dark=True, base_point_size=11)
gui = CerberusQtGUI(api, cameras, enable_simulation=True, auto_connect=True, dark=True)
gui.resize(760, 980)
gui.show()

results = {}
failures = []

def check(name, cond, detail=""):
    results[name] = bool(cond)
    if not cond:
        failures.append(f"{name}: {detail}")
    print(f"CHECK {'PASS' if cond else 'FAIL'}: {name} {detail}")

def shot(widget, name):
    p = os.path.join(SHOTS, name + ".png")
    widget.grab().save(p)
    print("shot", p)

def pump(seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.02)

def wait_until(pred, timeout, what):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.05)
    print("TIMEOUT waiting for", what)
    return False

def run():
    tab = gui.camera_tabs[0]
    cp = tab.camera_panel
    # 1. auto-connect
    ok = wait_until(lambda: api.state.get_camera(0).connected and api.state.get_camera(1).connected
                    and api.state.telescope_connected and api.state.filterwheel_connected and api.state.gps_connected,
                    20, "auto-connect")
    check("auto_connect_all", ok)
    pump(1.5)
    check("status_bar_shows_connected", "Connected" in gui.status_bar.camera_labels[0].text(), gui.status_bar.camera_labels[0].text())
    check("connect_btn_says_disconnect", cp.connect_btn.text() == "Disconnect", cp.connect_btn.text())
    check("exposure_shown_ms", cp.exposure_edit.text() == "1000", cp.exposure_edit.text())
    check("filter_combo_populated", cp.filter_combo.count() == 8, cp.filter_combo.count())
    shot(gui, "01_main_connected")

    # 2. exposure change via entry (100 ms)
    cp.exposure_edit.setText("100")
    cp._on_exposure_apply()
    ok = wait_until(lambda: abs((api.state.get_camera(0).exposure or 0) - 0.1) < 1e-6, 5, "exposure=0.1")
    check("exposure_set_100ms", ok, api.state.get_camera(0).exposure)
    # unit conversion display
    cp.unit_combo.setCurrentIndex(1); cp._on_unit_change(1)
    check("unit_convert_to_s", cp.exposure_edit.text() == "0.1", cp.exposure_edit.text())
    cp.unit_combo.setCurrentIndex(0); cp._on_unit_change(0)
    check("unit_convert_back_ms", cp.exposure_edit.text() == "100", cp.exposure_edit.text())

    # 3. start streaming with save on
    cp.save_cb.setChecked(True)
    cp.cube_spin.setValue(25)
    cp.target_edit.setText("SimTarget")
    cp.comment_edit.setText("driver test")
    cp._on_start_stop()
    ok = wait_until(lambda: api.state.get_camera(0).streaming, 8, "streaming")
    check("streaming_started", ok)
    ok = wait_until(lambda: api.state.get_camera(0).is_saving, 5, "saving")
    check("saving_started", ok)
    ok = wait_until(lambda: tab.live_panel.is_open(), 5, "live view auto-open")
    check("live_view_auto_opened", ok)
    lv = tab.live_panel.window_
    ok = wait_until(lambda: lv._last_result is not None and lv._last_result.frame_index >= 5, 10, "frames displayed")
    check("frames_displayed", ok, lv._last_result.frame_index if lv._last_result else None)
    pump(1.5)
    check("start_button_is_stop", cp.start_stop_btn.text() == "Stop", cp.start_stop_btn.text())
    check("timer_running", cp.stream_timer_label.text().startswith("Streaming"), cp.stream_timer_label.text())
    check("fps_reasonable", lv._last_result.fps > 3, lv._last_result.fps)
    shot(gui, "02_main_streaming")
    shot(lv, "03_liveview_streaming")

    # 4. FWHM target on the bright guide star (SkyModel star index 2 at (cx+40, cy-260)), auto scale on
    lv.auto_cb.setChecked(True)
    sky = api.cameras[0]._sky
    gx, gy = int(sky.star_x[2]), int(sky.star_y[2])
    lv._set_fwhm_target(gx, gy)
    ok = wait_until(lambda: lv._last_result is not None and lv._last_result.fwhm_arcsec is not None, 8, "fwhm measured")
    check("fwhm_measured", ok, lv._last_result.fwhm_arcsec if lv._last_result else None)
    if ok:
        fw = lv._last_result.fwhm_arcsec
        exp = api.sim_world.fwhm_arcsec()
        check("fwhm_close_to_sim", abs(fw - exp) < 0.35 * exp + 0.1, f"measured {fw:.3f} expected {exp:.3f}")

    # 5. guiding
    lv.guide_cb.setChecked(True); lv._on_guiding_clicked(True)
    ok = wait_until(lambda: "Guiding active" in lv.guiding_label.text() or "Drift" in lv.guiding_label.text(), 10, "guiding active")
    check("guiding_calibrated", ok, lv.guiding_label.text())
    # induce a drift: bump the telescope offset by 1" so guiding must correct
    off_before = api.sim_world.offset_ra_arcsec
    api.sim_world.apply_offset(1.0, 0.0)
    ok = wait_until(lambda: abs(api.sim_world.offset_ra_arcsec - (off_before + 1.0)) > 0.3, 25, "guiding correction applied")
    check("guiding_corrects_drift", ok, f"offset now {api.sim_world.offset_ra_arcsec:.2f} (was {off_before + 1.0:.2f})")

    # 6. photometry: target = variable star 0, comparison = star 1
    lv.phot_cb.setChecked(True)
    lv._set_target_aperture(int(sky.star_x[0]), int(sky.star_y[0]))
    lv._set_comparison_aperture(int(sky.star_x[1]), int(sky.star_y[1]))
    ok = wait_until(lambda: lv._last_result is not None and lv._last_result.relative_flux is not None, 8, "photometry")
    check("photometry_measured", ok, (lv._last_result.target_flux, lv._last_result.comp_flux, lv._last_result.relative_flux) if lv._last_result else None)
    pump(2.0)
    lv._show_lightcurve(); lv._show_fwhm_plot()
    pump(1.5)
    shot(lv, "04_liveview_fwhm_guiding_phot")
    shot(lv._lc_plot, "05_lightcurve")
    shot(lv._fwhm_plot, "06_fwhm_history")
    check("lightcurve_has_points", len(lv.worker.get_photometry_data()) > 10, len(lv.worker.get_photometry_data()))

    # 7. cursor readout via mouse move
    lv.view.fit_to_window(); pump(0.3)
    from PyQt6.QtCore import QPointF
    center = lv.view.viewport().rect().center()
    QTest.mouseMove(lv.view.viewport(), center); pump(0.3)
    check("cursor_readout", "=" in lv.cursor_label.text(), lv.cursor_label.text())

    # 8. stop streaming; stats
    cp._on_start_stop()
    ok = wait_until(lambda: not api.state.get_camera(0).streaming and not cp._busy, 10, "stopped")
    check("streaming_stopped", ok)
    pump(1.0)
    st = api.state.get_camera(0)
    check("frames_saved_gt0", st.frames_saved > 0, st.frames_saved)
    check("guiding_stopped_on_stop", not lv.guide_cb.isChecked() and not lv.worker.guiding.enabled)
    fits_files = glob.glob(os.path.join(SIM_DATA_DIR, "captures_*", "SIM1", "*.fits"))
    check("fits_written", len(fits_files) > 0, len(fits_files))
    check("saved_label", cp.saved_label.text() == str(st.frames_saved), (cp.saved_label.text(), st.frames_saved))

    # 9. N frames mode: 12 frames then auto-stop
    cp.save_cb.setChecked(False)
    cp.nframes_edit.setText("12")
    cp._on_start_stop()
    ok = wait_until(lambda: api.state.get_camera(0).streaming, 8, "n-frames streaming")
    ok = wait_until(lambda: not api.state.get_camera(0).streaming and not cp._busy, 20, "n-frames auto stop")
    check("nframes_autostop", ok, cp.progress_label.text())
    check("nframes_progress_done", cp.progress_label.text().startswith("Done"), cp.progress_label.text())
    cp.nframes_edit.setText("")

    # 10. ROI via subarray panel while streaming; saving auto-resumes
    cp.save_cb.setChecked(True)
    cp._on_start_stop()
    wait_until(lambda: api.state.get_camera(0).is_saving, 8, "saving again")
    sp = tab.subarray_panel
    lv._on_roi_dragged(gx - 200, gy - 150, gx + 200, gy + 150)
    ok = wait_until(lambda: not sp._busy and api.state.get_camera(0).streaming, 15, "roi applied + restarted")
    check("roi_applied_restarted", ok)
    ok = wait_until(lambda: lv._last_result is not None and lv._last_result.shape[1] < SIZE[0], 10, "roi frames")
    check("roi_frame_shape", ok, lv._last_result.shape if lv._last_result else None)
    check("subarray_enabled_cb", sp.enable_cb.isChecked())
    ok = wait_until(lambda: api.state.get_camera(0).is_saving, 8, "saving resumed after ROI")
    check("saving_resumed_after_roi", ok)
    pump(1.0)
    shot(gui, "07_main_roi")
    shot(lv, "08_liveview_roi")
    # reset to full frame
    sp._on_reset()
    ok = wait_until(lambda: not sp._busy and api.state.get_camera(0).streaming, 15, "reset restarted")
    ok = wait_until(lambda: lv._last_result is not None and lv._last_result.shape[1] == SIZE[0], 10, "full frame back")
    check("subarray_reset_full_frame", ok, lv._last_result.shape if lv._last_result else None)
    cp._on_start_stop()
    wait_until(lambda: not api.state.get_camera(0).streaming and not cp._busy, 10, "stopped 2")

    # 11. filter change from combo
    idx = cp.filter_combo.findText("g")
    cp.filter_combo.setCurrentIndex(idx); cp._on_filter_activated(idx)
    ok = wait_until(lambda: api.state.current_filter == "g", 10, "filter g")
    check("filter_changed", ok, api.state.current_filter)
    ok = wait_until(lambda: api.state.telescope_focus is not None and abs(api.state.telescope_focus - cfg.get_filter_focus_position("g")) < 0.05, 10, "focus preset")
    check("filter_focus_preset_applied", ok, api.state.telescope_focus)

    # 12. windows
    gui.open_camera_settings(); gui.open_telescope_window(); gui.open_focus_window()
    pump(1.5)
    csw, tw, fw = gui._camera_settings_window, gui._telescope_window, gui._focus_window
    check("camera_settings_table_filled", csw.table.rowCount() > 10, csw.table.rowCount())
    check("telescope_window_ra", tw.ra.text() != "--", tw.ra.text())
    check("focus_window_filters", len(fw._filter_checks) == 8, len(fw._filter_checks))
    # camera settings: set binning 2x2 via dropdown handler
    csw._on_setting("BINNING", 2.0)
    ok = wait_until(lambda: str(api.state.get_camera(0).params.get('BINNING')) == '2x2', 8, "binning")
    check("binning_set_via_window", ok, api.state.get_camera(0).params.get('BINNING'))
    csw._on_setting("BINNING", 1.0); pump(1.0)
    # telescope nudge
    off0 = api.sim_world.offset_dec_arcsec
    tw.step.setValue(2.0); tw._nudge(0, +1)
    ok = wait_until(lambda: abs(api.sim_world.offset_dec_arcsec - off0 - 2.0) < 1e-6, 8, "nudge N")
    check("telescope_nudge_north", ok, api.sim_world.offset_dec_arcsec - off0)
    shot(csw, "09_camera_settings"); shot(tw, "10_telescope")

    # 13. manual focus go
    fw.goto_spin.setValue(27.0); fw._on_focus_go()
    ok = wait_until(lambda: api.state.telescope_focus is not None and abs(api.state.telescope_focus - 27.0) < 0.05, 15, "focus 27")
    check("manual_focus_go", ok, api.state.telescope_focus)

    # 14. focus loop (short, single filter 'g' selected only)
    for name, cb in fw._filter_checks.items():
        cb.setChecked(name == "g")
    fw.start_spin.setValue(25.0); fw.end_spin.setValue(28.0); fw.step_spin.setValue(0.5)
    fw.exp_spin.setValue(0.05); fw.settle_spin.setValue(0.05)
    fw._on_run()
    ok = wait_until(lambda: not fw._running, 120, "focus loop")
    check("focus_loop_completed", ok, fw.progress_label.text())
    check("focus_loop_result_ok", fw.progress_label.text().startswith("Done"), fw.progress_label.text())
    check("focus_results_table", fw.results_table.rowCount() == 1, fw.results_table.rowCount())
    pump(1.0)
    shot(fw, "11_focus_window")
    check("config_not_overwritten", api.config_save_path != None and os.path.exists(api.config_save_path), api.config_save_path)

    # 15. camera 2 tab independent streaming
    gui.tabs.setCurrentIndex(1)
    tab2 = gui.camera_tabs[1]
    tab2.camera_panel._on_start_stop()
    ok = wait_until(lambda: api.state.get_camera(1).streaming, 8, "cam2 streaming")
    check("camera2_streams", ok)
    pump(2.0)
    check("camera1_not_streaming", not api.state.get_camera(0).streaming)
    shot(gui, "12_main_cam2")
    shot(tab2.live_panel.window_, "13_liveview_cam2")
    tab2.camera_panel._on_start_stop()
    wait_until(lambda: not api.state.get_camera(1).streaming, 10, "cam2 stop")

    # 16. light theme screenshot
    gui.act_dark.setChecked(False); pump(0.5); gui.tabs.setCurrentIndex(0); pump(0.5)
    shot(gui, "14_main_light")
    gui.act_dark.setChecked(True); pump(0.3)

    # 17. shutdown path
    from PyQt6.QtWidgets import QMessageBox
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    gui.close()
    ok = wait_until(lambda: gui._force_close, 30, "shutdown")
    check("shutdown_completed", ok)
    check("all_disconnected", not api.state.camera_connected and not api.state.telescope_connected, api.state.to_dict()['cameras'])

    print("\n===== SUMMARY: %d/%d checks passed" % (sum(results.values()), len(results)))
    for f in failures:
        print("  FAILED:", f)
    app.quit()

QTimer.singleShot(200, run)
rc = app.exec()
sys.exit(0 if not failures else 2)
