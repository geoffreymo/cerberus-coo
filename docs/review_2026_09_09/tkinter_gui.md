# Cerberus-COO Tkinter GUI review (read-only)

Scope: `gui/app.py`, `gui/panels/{camera_controls,subarray_panel,image_display,status_bar}.py`,
`gui/{focus_window,camera_settings_window,telescope_settings_window}.py`, dead-code candidates in
`gui/panels/`, plus the API contract in `api/cerberus.py` / `api/state.py` as consumed by the GUI.
Reviewed on 2026-09-09. Note: `api/cerberus.py`, `gui/focus_window.py`, `gui/panels/focus_panel.py`,
`guiding/engine.py` and `config.json` were modified on disk by someone else during this review
(the visible edits were `from config import` -> `from ..config import` and config additions);
every line number quoted below was re-verified against the files as they stand at 11:09.

New findings are numbered G50-G78. Line numbers are `file:line` in the current tree.

---

## 1. Summary table

| ID | Severity | File:line | One-line summary |
|----|----------|-----------|------------------|
| G50 | CRITICAL | `gui/app.py:302-311` + `api/cerberus.py:1737-1740` | API fires status callbacks while holding `_callback_lock`; GUI callback does a cross-thread `root.after()` that blocks until the Tk loop is idle -> lock-order deadlock with the 1 Hz `update_status()` timer (permanent GUI hang) |
| G51 | HIGH | `gui/panels/image_display.py:654-662` | Guiding is fed per-frame *deltas* (box re-centres every frame) but `GuidingEngine` expects absolute positions -> reference ~ 0, drift ~ per-frame velocity, corrections essentially never fire |
| G52 | HIGH | `gui/app.py:417-430, 489-527` | Cancelling the Quit dialog permanently stops the 1 Hz status poll (`_closing=True` set before the dialog; `_update_status` returns without rescheduling) |
| G53 | HIGH | `gui/panels/subarray_panel.py:128-150, 176-201, 259-284` | Any subarray change while saving silently ends the save (stop_streaming stops saving); restart does not resume it; Save checkbox stays ticked; guiding/N-frames/timer not reset |
| G54 | HIGH | `gui/app.py:84`, `gui/panels/camera_controls.py:594-595` | Non-visible notebook tab is never updated -> N-frames auto-stop and per-camera stats do not run for the hidden camera |
| G55 | HIGH | `camera_controls.py:285,300,440,536`, `subarray_panel.py:130-150,272-278`, `image_display.py:395/486` | Hardware calls on the GUI thread: Start sleeps up to ~1.1 s (align-to-second), Stop >= 0.2-0.4 s + up to 60 s join, ROI apply = 5x(`set_property`+full `update_status`) inside the OpenCV mouse callback; #46 still present |
| G56 | HIGH | `gui/panels/image_display.py:680,1249` vs `focusloop/focus_analyzer.py:369` | `matplotlib.use('TkAgg')` (GUI) vs `matplotlib.use('Agg')` (focus thread): backend switch closes ALL open figures from a background thread -> running a focus loop destroys live FWHM/lightcurve windows via cross-thread Tk calls |
| G57 | HIGH | `gui/app.py:432-477, 489-523`, `__main__.py:161` | Auto-connect daemon thread is neither cancellable nor joined; close/`Dcamapi.uninit()` can race a camera connect in progress; status callback never unregistered |
| G58 | MEDIUM | `gui/panels/camera_controls.py:281,328,522-538` | #45 confirmed (stale copy -> N-frames never auto-stops after first run); progress `StringVar` is never bound to a widget; auto-stop polled at 1 Hz (overshoot); auto-stop path does not stop guiding |
| G59 | MEDIUM | `gui/app.py:69-78`, `subarray_panel.py:152-201`, `image_display.py:466-469` | `set_current_subarray_offset()` only called from drag/reset, not from manual Apply -> nested SHIFT-drag uses wrong absolute coordinates; FWHM target / apertures not cleared on ROI change |
| G60 | MEDIUM | `gui/panels/subarray_panel.py:194-197, 272-278` | Subarray properties set MODE=ON first then HPOS before HSIZE, return values ignored -> order-dependent partial application; manual Apply never sets MODE |
| G61 | MEDIUM | `gui/panels/camera_controls.py:622-626` | `style='Active.TCheckbutton'` is never defined anywhere -> "saving" highlight is a no-op; `save_var` never synced from `cam_state.is_saving` |
| G62 | MEDIUM | `gui/focus_window.py:498-514, 637-638, 709-714` | #9 still present (direct `progress_var.set` from thread); no `destroy()` override (#21); in sim mode `update_from_state` re-enables Start / disables Abort 1 s into the run and Abort cannot stop the sim loop |
| G63 | MEDIUM | `gui/focus_window.py:189-192, 276, 457, 463` | Hard-coded `get_camera(0)`, `/data/cerberus/...` path (ignores `config.paths` and the GUI Save Path), focus defaults and multiplier label text; mock hardware duplicated with `focusloop/test_focus_loop.py` |
| G64 | MEDIUM | `gui/camera_settings_window.py:283, 344-353` | Treeview rebuilt from scratch every second -> scroll/selection reset; property changes allowed while streaming, failures silently reverted after 1 s. (Space-separated keys and `1.0`-vs-`1` are verified NOT bugs.) |
| G65 | MEDIUM | `gui/panels/image_display.py:889-890` | `except Exception: pass` around the whole display loop hides every rendering/fit/OpenCV error |
| G66 | MEDIUM | `gui/panels/image_display.py:971-982, 897-899` | "FPS" shows the display-loop rate (capped ~20 Hz by the 50 ms `after` loop), not the camera frame rate |
| G67 | MEDIUM | `gui/panels/camera_controls.py:126, 355-390, 597-620` | Exposure only applied on `<Return>`; focus-out reverts within 1 s; `_last_unit` assigned twice so an empty entry + unit change desynchronises the next conversion |
| G68 | MEDIUM | `gui/panels/camera_controls.py:392-412, 576-586`; `api/cerberus.py:1554-1555` | #20 partially present: no failure notification; during a wheel move the 1 Hz poll flips the combobox back to the old filter (poll reads `filterwheel.filter` without `_filterwheel_lock`) |
| G69 | MEDIUM | `gui/panels/image_display.py:742, 1313, 1308` | Re-opening a plot replaces the only `FuncAnimation` reference -> previous window freezes; `std_rel/mean_rel` divides by zero when mean is 0 |
| G70 | MEDIUM | `gui/panels/image_display.py:1039-1041` (via `guiding/engine.py:172`) | Guiding correction `api.move_offset()` is a synchronous TCS round trip executed inside the display loop on the GUI thread |
| G71 | LOW | `camera_controls.py:27,49,50`, `subarray_panel.py:31,33,211,213`, `app.py:430` | Defaults hard-coded instead of config: output dir, cube size (100 vs config 1000), object name, 4096x2304 full frame, 1000 ms poll interval |
| G72 | LOW | `gui/panels/camera_controls.py:33, 414-428` | `readout_var` never synced from camera params; shows stale value after reconnect |
| G73 | LOW | `gui/panels/status_bar.py:105`, `telescope_settings_window.py:180` | Truthiness checks: focus `0.0` / airmass `0.0` display as `--` |
| G74 | LOW | `gui/app.py:504-519` | Close path never disconnects GPS, does not abort a running focus loop or guiding, and double-calls `stop_saving` from a stale copy |
| G75 | LOW | `gui/panels/subarray_panel.py:104, 161-164` | Label says "rounded to nearest 4" but code floors; no range / minimum-size validation on the manual path |
| G76 | LOW | `gui/panels/subarray_panel.py:203-217` | Reset always goes through the stop/apply/restart chain even when subarray is already OFF |
| G77 | LOW | `gui/panels/__init__.py:10-12`, `app.py:479-487`, `image_display.py:50,800,892,993-995`, `subarray_panel.py:219-227` | Dead code: unused panels still exported, legacy `_auto_connect_filterwheel`, uncontended `_display_lock`, no-op `update_from_state`s, unused `display_size` |
| G78 | LOW | `gui/app.py:229-245`, hint labels `('TkDefaultFont', 9)` | Font family "Ubuntu" hard-coded; hint labels ignore the 14 pt base font |

---

## 2. Findings

### G50 - CRITICAL - Lock-order deadlock between API status callbacks and the Tk main loop

**Where.** `gui/app.py:302-311`:
```python
def _on_status_change(self, state):
    self._last_state = state
    if not self._update_pending:
        self._update_pending = True
        self.root.after(50, self._do_update_panels)
```
`api/cerberus.py:1733-1742`:
```python
def _notify_status_change(self):
    state_copy = self.state
    with self._callback_lock:
        for callback in self._status_callbacks:
            try:
                callback(state_copy)
```
`_notify_status_change` is invoked synchronously on whatever thread called the API. The GUI calls the API from several background threads: the auto-connect thread (`gui/app.py:436-477`: `connect_telescope`, `connect_camera`, `connect_filterwheel`, `connect_gps`), the Connect button thread (`camera_controls.py:246-251`), the filter-change thread (`camera_controls.py:400-409` -> `set_filter` -> possibly `set_focus`, two notifications), focus Go/+/- threads (`focus_window.py:648-688`), the TCS connect thread (`telescope_settings_window.py:133-138`) and the focus-loop thread (`api.run_focus_loop`, `focus_window.py:442-447`).

**Mechanism.** CPython's `_tkinter` (threaded Tcl build, the Linux default) does not execute Tk commands on a foreign thread; it queues the call as a Tcl event to the main thread and **blocks the calling thread until the main loop services that event**. So `root.after(50, ...)` from thread B returns only when the main thread M is back in `Tcl_DoOneEvent`. While B waits, it still holds `api._callback_lock` (non-reentrant `threading.Lock`).

**Failure scenario.** M runs the 1 Hz timer `_update_status` (`app.py:417-430`) -> `api.update_status()` (`api/cerberus.py:1534`), which spends tens to hundreds of ms in TCS/DCAM reads and then calls `_notify_status_change` -> `with self._callback_lock`. If B entered `_notify_status_change` in that window, B holds the lock and is waiting for M; M is inside a Python callback (not servicing events) and is waiting for the lock. Neither side has a timeout. The GUI freezes permanently. The window is hit every second and every background API call performs 1-3 notifications, so a startup (4 notifications) or a filter change (2-3) has a realistic chance of hanging the application. This also explains why "the whole GUI stops" symptoms would be intermittent and hard to reproduce.

**Fix (Tk).** In the API, copy the callback list under the lock and invoke callbacks *outside* it. In the GUI, never call Tk from `_on_status_change`; push `state` into a `queue.Queue` and have a 50 ms `after` poller on M drain it.
**Fix (Qt).** Emit a `pyqtSignal(object)` from `_on_status_change`; cross-thread emit is queued and non-blocking. Do **not** use `QTimer.singleShot(0, ...)` from a foreign thread as the current `gui_qt/app.py:233` draft does - Python threads are not `QThread`s and that timer will not fire.

### G51 - HIGH - Guiding engine receives per-frame deltas instead of absolute star positions

**Where.** `gui/panels/image_display.py:654-662`:
```python
half_box = self._fwhm_box_size // 2
dx, dy = centroid_offset
edge_margin = half_box * 0.6
if abs(dx) < edge_margin and abs(dy) < edge_margin:
    cx, cy = self._fwhm_target
    self._fwhm_target = (cx + int(round(dx)), cy + int(round(dy)))
    self._guiding_engine.add_measurement(centroid_offset[0], centroid_offset[1])
```
`centroid_offset` is the star's offset from the centre of the *current* cutout (`image_display.py:625`). The line above moves the cutout onto the star every frame, so the next measurement is again ~0 (plus sub-pixel rounding residual plus whatever the star moved *since the previous frame*).

`guiding/engine.py:92-141` treats the values as absolute coordinates: the reference is the mean of the first `min_samples` values (`_update_calibration`), drift is `mean(recent) - reference`. With deltas, reference ~ (0,0) and "drift" ~ mean per-frame motion. At 0.051"/px and a 0.1" threshold the star must jump >= 2 px *between consecutive frames* for a correction to fire; slow drift (the thing guiding exists for) is invisible. When a correction does fire it is the per-frame velocity x gain, not the accumulated error.

**Fix.** Feed `add_measurement(cx + dx, cy + dy)` (absolute sensor coordinates of the fitted centroid, before re-centring the box), and keep the reference in the same absolute frame. Add a unit test that drifts a synthetic star 0.02 px/frame for 30 s and asserts a correction.

### G52 - HIGH - Cancelling the Quit dialog kills the status poll

**Where.** `gui/app.py:489-494`:
```python
def _on_close(self):
    self._closing = True
    if messagebox.askokcancel("Quit", "Are you sure you want to quit?"):
```
and `gui/app.py:417-430`:
```python
def _update_status(self):
    if self._closing:
        return          # <- no reschedule
    ...
    self.root.after(1000, self._update_status)
```
**Scenario.** The dialog is modal but the Tk loop keeps running; within <= 1 s the timer fires, sees `_closing`, and returns without rescheduling. The user presses Cancel; `_closing` becomes `False` (`app.py:526`) but nothing restarts the chain. From then on `api.update_status()` is never called, so temperature, TCS position, frames captured/saved, filter name and the N-frames auto-stop (all driven by that poll) freeze for the rest of the session. Only the display window and the stream timer keep moving.
**Fix.** Set `_closing` only after the user confirms, or reschedule unconditionally and gate only the body.

### G53 - HIGH - Subarray changes while saving silently stop the save

**Where.** `gui/panels/subarray_panel.py:128-134` (enable toggle), `176-183` (Apply), `259-266` (drag ROI) all do `self.api.stop_streaming(...)` then `after(200, apply)` then `after(100, lambda: self.api.start_streaming(...))` (`:150`, `:201`, `:284`).
`api/cerberus.py:349-353`: `stop_streaming` calls `stop_saving` when the camera is saving. The restart calls `start_streaming` only; nothing calls `start_saving` again, nothing tells `CameraControlsPanel`.
**Scenario.** Observer is saving cubes, SHIFT-drags a smaller ROI to speed up. Saving ends, the Save checkbox is still ticked (`save_var` is never synced from `cam_state.is_saving`, see G61), the stream timer keeps counting from the original start, and the status bar goes from "Saving (n)" to "Streaming" - easy to miss at night. Also not handled by the restart: guiding keeps running on frames of a different geometry, `_taking_images` keeps its stale `_start_frame_count` (G58), and the display window is not re-positioned.
**Fix.** Route stop/apply/restart through one owner (camera panel or API) that captures `is_saving`, `taking_images`, guiding state, and restores them, or forbid ROI changes while saving with a clear message.

### G54 - HIGH - Hidden notebook tab never updates (multi-camera)

**Where.** `gui/app.py:80-87`:
```python
def update_from_state(self, state):
    try:
        if not self.winfo_exists() or not self.winfo_viewable():
            return
```
`ttk.Notebook` unmaps non-selected tabs, so `winfo_viewable()` is false for every camera but the visible one. `CameraControlsPanel.update_from_state` is therefore skipped for hidden cameras, including the N-frames check at `camera_controls.py:594-595` and the frame/saved/dropped counters. The comment at `app.py:345` ("Always update...") contradicts the code.
**Scenario.** PHX2 tab visible, PHX3 started with N Frames = 100: PHX3 keeps streaming until the user clicks its tab, at which point it "instantly" stops. Drop counters for the hidden camera are also invisible.
**Fix.** Always update model state; only skip expensive *widget* work when hidden (or in Qt, just update - Qt widgets are cheap to update when hidden).

### G55 - HIGH - Blocking hardware calls on the GUI thread (extends #46)

Verified chains, all on the Tk main thread:
- **Start** (`camera_controls.py:314`) -> `api.start_streaming` -> `hardware/camera/controller.py:536-544` sleeps until the next integer second + 0.10 s (up to ~1.1 s) *on the caller's thread*. Every Start freezes the GUI for up to 1.1 s.
- **Stop** (`camera_controls.py:285`) -> `controller.py:575` `time.sleep(0.2)` always; if saving, `api/cerberus.py:352` sleeps another 0.2 s and `stop_saving` joins the writer for up to 60 s (`api/cerberus.py:718`). Then `camera_controls.py:298-300` sleeps 0.05 s and calls `stop_saving` again from the stale copy.
- **Save checkbox off** (`camera_controls.py:440`) -> same 60 s join.
- **N-frames auto-stop** (`camera_controls.py:536`) -> `stop_streaming` from inside `update_from_state`, i.e. inside the coalesced status-callback handler.
- **Readout / camera-settings combobox** (`camera_controls.py:425`, `camera_settings_window.py:189-254`) -> `api.set_camera_property` -> `controller._update_camera_params()` (enumerates every DCAM property) **and** `api.update_status()` (`api/cerberus.py:295`: TCS x3, filter wheel, all cameras).
- **ROI** (`subarray_panel.py:272-278`) -> 5 x the above, i.e. 5 full hardware polls, then `start_streaming` with the alignment sleep. Total 2-5 s. The drag variant is entered from the OpenCV mouse callback, which runs inside `cv2.waitKey(1)` inside `_display_loop` (`image_display.py:395,486,884`), so the first `stop_streaming` (>= 0.2 s) executes *inside the mouse handler while `_display_lock` is held*.
- **Disconnect** (`camera_controls.py:238`) -> `controller.py:197` joins the camera thread up to 5 s.
- **Status poll** (`app.py:425`) -> `update_status()` every second on the GUI thread (this is #46, still present).
**Fix.** Put a single worker (QThread or `concurrent.futures`) in front of the API; the GUI only enqueues commands and consumes state via signals. Move the align-to-second wait into the camera thread.

### G56 - HIGH - Two `matplotlib.use()` calls with different backends, one from a worker thread

**Where.** `gui/panels/image_display.py:680` and `:1249` call `matplotlib.use('TkAgg')`; `focusloop/focus_analyzer.py:369` calls `matplotlib.use('Agg')`, and the analyzer runs on the focus-loop thread (`focus_window.py:442-447` -> `api.run_focus_loop`).
`matplotlib.use()` after pyplot has been imported calls `pyplot.switch_backend()`, which closes every open figure when the backend actually changes (deprecated-but-still-done in 3.8; the deprecation warning text says exactly this).
**Scenario.** Observer has the live FWHM plot open (TkAgg figure with a `FuncAnimation` on Tk timers), then runs a focus loop. The analyzer's `use('Agg')` executes on the worker thread, closes the Tk figure from a non-main thread (cross-thread Tk destroy, subject to the same marshalling/deadlock hazard as G50), the plot window vanishes, and the global backend is now Agg. Next click on "Plot" switches back to TkAgg (closing the analyzer's Agg figures, harmless).
**Fix.** Never call `matplotlib.use()` at runtime in library code; the analyzer should build figures with `matplotlib.figure.Figure` + `FigureCanvasAgg` directly. The GUI should embed a canvas (Qt: `FigureCanvasQTAgg` or pyqtgraph) instead of `pyplot`.

### G57 - HIGH - Auto-connect thread is uncontrolled; close and DCAM uninit can race it

**Where.** `gui/app.py:432-477` starts `threading.Thread(target=connect_thread, daemon=True)` 500 ms after startup; the thread object is not stored, not joined, has no cancel flag. `_on_close` (`app.py:489-523`) immediately calls `disconnect_camera`/`disconnect_telescope` and `root.destroy()`; `__main__.py:161` then calls `Dcamapi.uninit()`.
**Scenario.** Camera connection takes several seconds. Operator closes the window during startup: `disconnect_camera` and `Dcamapi.uninit()` run while `controller.connect()` is executing on the daemon thread (violates the "camera operations on the camera thread" rule in CLAUDE.md), and the daemon is killed mid-DCAM-call at interpreter exit. Also `api.on_status_change(self._on_status_change)` (`app.py:165`) is never removed, so any notification after `root.destroy()` (e.g. from that thread) raises `TclError` inside the API's callback loop (logged as "Error in status callback").
**Fix.** Keep a handle; set an `Event` on close; join with timeout before disconnecting/uninit; call `api.remove_status_callback` before destroying the window.

### G58 - MEDIUM - N-frames mode (#45 confirmed, plus three more defects)

1. **Stale copy** - `camera_controls.py:281` `cam_state = self.api.state.get_camera(...)` (deep copy, `api/cerberus.py:1481`), `:314` `start_streaming` resets the real counter to 0 (`api/cerberus.py:328`), `:328` `self._start_frame_count = cam_state.frames_captured` reads the *old* value. `_check_image_progress` (`:525`) then computes `current - old_count`, negative until the new run exceeds the previous run. Proposed fix (`_start_frame_count = 0`) is correct because the API always resets on start; better, read `self.api.state` *after* `start_streaming` returns.
2. **Progress never shown** - `self.image_progress_var` (`:37, :305, :329, :526, :531`) is never attached to any Label. The "Taking: n / N" / "Done" text is invisible.
3. **1 Hz granularity** - the check runs only inside `update_from_state`, which is driven by `update_status()` at 1 Hz (frame callbacks do not notify). At 100 fps, N = 10 captures ~100+ frames. For Qt: implement N-frames in the API/camera thread (`start_streaming(n_frames=N)`), not by polling GUI state.
4. **Asymmetry with manual Stop** - manual Stop (`:292-294`) disables guiding; the auto-stop (`:534-538`) does not, so `_guiding_engine.enabled` stays True with no frames.

### G59 - MEDIUM - Nested-ROI offset only tracked for the drag path

**Where.** `gui/panels/image_display.py:466-469` adds `self._current_hpos/_vpos` to convert display-frame pixels into absolute sensor coordinates. Those are set only by `set_current_subarray_offset` (`:495`) which is called from `app.py:73` (after a drag) and `app.py:78` (after Reset). The manual path `subarray_panel._on_apply` (`:152-201`) never notifies the display panel.
**Scenario.** Type HPOS=1000/VPOS=500 and Apply, then SHIFT-drag a star at display (100,100): the GUI requests HPOS=100 (should be 1100). Additionally `_fwhm_target`, `_target_aperture`, `_comparison_aperture` are kept across ROI changes; they now point at different sky (bounds checks prevent crashes, but the F/T/C markers silently sit on the wrong star).
**Fix.** Single source of truth: the display panel should read the current HPOS/VPOS from `cam_state.params` (`SUBARRAY HPOS`...) each frame, and clear markers when the frame shape changes.

### G60 - MEDIUM - Subarray property ordering and unchecked results

**Where.** `subarray_panel.py:272-278` sets `SUBARRAY_MODE=2.0` (ON) first, then `HPOS`, `HSIZE`, `VPOS`, `VSIZE`; `:194-197` (manual Apply) sets the four without MODE at all; all return values are discarded. `controller.set_property` (`hardware/camera/controller.py:722-727`) returns False and logs when DCAM rejects a value.
**Risk.** With MODE ON, DCAM constrains `HPOS <= width - HSIZE`; setting `HPOS=1000` while `HSIZE` is still 4096 can be rejected, leaving HPOS=0 with the new size. Because results are ignored the GUI reports success ("ROI applied") regardless. I could not execute against hardware, so treat as a verification item: the documented-safe order is MODE OFF -> sizes -> positions -> MODE ON, checking each result, and the old v18 GUI (`package/cerberus_gui_test.py:1281-1299`) also set MODE separately from the parameters.

### G61 - MEDIUM - Save highlight style undefined; checkbox state never synced

`camera_controls.py:622-626`:
```python
if cam_state.is_saving:
    self.save_checkbox.config(style='Active.TCheckbutton')
else:
    self.save_checkbox.config(style='TCheckbutton')
```
`grep -rn "Active.TCheckbutton"` finds only this use; no `Style.configure('Active.TCheckbutton', ...)` exists. ttk resolves an undefined style to its parent layout, so there is no visual change - the intended "saving is active" cue does not exist. Combined with the fact that `save_var` is never set from `cam_state.is_saving`, a save that ends on its own (G53, writer failure, `start_saving` returning False after the fact) leaves a ticked box and no indication.

### G62 - MEDIUM - Focus window threading and button-state logic

- `focus_window.py:498-514` (real-hardware path) still calls `self.progress_var.set(...)` directly on the worker thread (#9). The simulated path (`:580-625`) correctly uses `after`. In CPython this is a blocking cross-thread call rather than an outright crash, but it is unsupported and participates in the G50 hazard.
- No `destroy()` override: closing the Toplevel mid-run neither aborts nor joins `_focus_thread` (#21/#22). The loop keeps moving the telescope focus and taking exposures with no visible UI. (Mitigation that exists: reopening the window shows Abort enabled via `state.focus_loop_running`, so the *real* loop can still be aborted; the *simulated* loop cannot.)
- `update_from_state` (`:709-714`) sets Start/Abort from `state.focus_loop_running`, which only the API sets. In simulation mode the loop bypasses the API, so 1 s after pressing Run the poll re-enables Start and disables Abort; a second overlapping sim loop can be started. `_on_abort` (`:637`) calls `api.abort_focus_loop()`, which has no handle on the sim loop, so Abort is a no-op there.
- `FocusLoop._report_progress` (`focusloop/focus_sequence.py:175-184`) does not guard the callback; any exception raised in the GUI's `on_progress` aborts the whole run via `run_focus_loop`'s `except`.

### G63 - MEDIUM - Focus window hard-coding and duplication

- `focus_window.py:457` `self.api.state.get_camera(0)` - if camera indices do not start at 0 (`enumerate_cameras` uses DCAM device indices), this creates an empty `CameraState` in the copy and the folder becomes `cam0`. Should use the selected tab's camera or `api.get_cameras()[0]`.
- `:463` `output_dir = f"/data/cerberus/captures_{date_str}/{camera_id}/focus"` ignores `config.paths.default_output_dir` and the Save Path the observer typed in the camera panel. (`config.paths.focus_output_dir` also exists and is likewise ignored.)
- `:189-192` defaults `30.0 / 45.0 / 0.25 / 1.0` ignore `config.focusloop.*` (config has exposure 5.0 s).
- `:276` label text "Multipliers: R/G/I=3x, Z=5x, U/Ha/OIII=10x" is static while the values come from `config.focusloop.exposure_multipliers` (now present in `config.json:62-71`).
- `:34-170` (`make_multi_star_image`, `focus_to_fwhm`, `MockTelescopeStatus`, `MockTelescope`, `MockCamera`, `MockFilterWheel`) duplicate `focusloop/test_focus_loop.py:75-288` almost verbatim. Keep one copy in `focusloop/` (e.g. `focusloop/mock_hardware.py`) and import it from both.
- `#39` hard-coded plate scale: no longer present in `focus_window.py` (the analyzer/config own it) - see section 3.

### G64 - MEDIUM - Camera settings window

- `camera_settings_window.py:283` -> `_update_params_tree` (`:344-353`) deletes and re-inserts every row on every status update (1 Hz). The Treeview scroll position and selection reset each second; the list is unusable while scrolling.
- Property-key naming: `params.get('SENSOR MODE')`, `'TRIGGER SOURCE'`, etc. (`:298-334`) use DCAM's space-separated names. This is **correct**: `controller._update_camera_params` (`hardware/camera/controller.py:913-918`) keys the dict by `dcam.prop_getname()` and the controller itself reads `'TRIGGER SOURCE'` (`:927`). Values are `valuetext or propvalue`, so enum props arrive as text ("EXTERNAL", "ON") and the string matching works.
- `binning_val` float vs int keys (`:291-295`): `1.0 in {1: ...}` is True in Python (equal hash/eq), so this is **not** a bug either. `binning_val == 0` would be skipped by the truthiness check, but BINNING is never 0.
- Handlers `_on_*_change` (`:180-256`) do not check `cam_state.streaming`; DCAM refuses most of these while capturing, `set_camera_property` returns False, nothing is shown, and the combobox snaps back on the next poll. Should disable the controls while streaming.
- Each handler triggers G55's double full poll on the GUI thread.

### G65 - MEDIUM - Silent exception swallowing in the display loop

`image_display.py:889-890`:
```python
except Exception as e:
    pass
```
wraps window creation, frame fetch, scaling, FWHM fit, photometry, guiding status, overlays, `imshow` and `waitKey`. Any bug (shape mismatch after ROI change, cv2 failure, scipy import error, Tk var error) is invisible; the loop simply shows a stale frame. At least `logger.exception` with rate limiting; in Qt, let the painter raise.

### G66 - MEDIUM - "FPS" is display FPS, not camera FPS

`_update_stats` (`image_display.py:971-982`) counts frames *pulled by the display loop*, which runs every 50 ms (`:899`) and takes one frame per pass (`:817`). The reading saturates near 20 even at 500 fps. `cam_state.frame_rate` (from `controller.get_frame_rate()`) is the real number and is already polled; show that, label the other "display fps" if kept.

### G67 - MEDIUM - Exposure entry semantics

- Applied only on `<Return>` (`camera_controls.py:126`). `update_from_state` (`:599`) refreshes the entry from the camera whenever the entry does not have focus, so typing a value and clicking elsewhere reverts within 1 s with no feedback. The echo-back of the camera's *actual* exposure is a good behaviour to keep (DCAM quantises); the silent discard is not.
- `_on_unit_change` (`:355-390`) sets `self._last_unit = new_unit` inside the try (`:384`) and again unconditionally at `:390`. If the entry is empty/non-numeric, the conversion is skipped but the unit still advances, so the next successful conversion uses the wrong base unit.
- `focus_get()` can raise `KeyError` for the Combobox popdown listbox (not a registered widget); only `TclError, AttributeError` are caught at `:618`. It is caught further up by the `'popdown'` check in `app.py:366-371`, but the rest of that panel's update (stats, drop colour) is skipped for that tick.

### G68 - MEDIUM - Filter change feedback and flicker (#20)

`camera_controls.py:392-412` starts a thread; `set_filter` returns False on failure and nothing is shown (#20). Independently, while the wheel is moving (`api.set_filter` holds `_filterwheel_lock`), the 1 Hz `update_status` reads `self.filterwheel.filter` (`api/cerberus.py:1554-1555`) without that lock and writes it to state; `update_from_state` (`:582-583`) then sets the combobox back to the *old* filter until the move completes, when it flips to the new one. Concurrent EFW library calls from two threads are also a hardware-layer concern.

### G69 - MEDIUM - Plot window lifetime and a division by zero

- `image_display.py:742` / `:1313` store the `FuncAnimation` in a single attribute; opening the plot a second time drops the only reference to the first animation, which is then garbage-collected and its window stops updating while still open.
- `:1308` `std_rel/mean_rel*100` raises `ZeroDivisionError` if every relative flux is 0 (target flux 0 after background subtraction). The exception surfaces inside the Tk timer callback.

### G70 - MEDIUM - Guiding correction is a synchronous TCS command on the GUI thread

`image_display.py:1039-1041` `return self.api.move_offset(ra_arcsec, dec_arcsec)`; called from `GuidingEngine._apply_guiding_correction` (`guiding/engine.py:172`) which runs inside `_update_fwhm` inside `_display_loop`. Each correction blocks rendering for the TCS round trip and holds `_display_lock`. Should be dispatched to the hardware worker.

### G71 - LOW - Hard-coded defaults that duplicate config

`camera_controls.py:27` `"Object"` (config `gui.default_object_name`), `:49` `"/data/cerberus"` (config `paths.default_output_dir`), `:50` `"100"` frames/cube (config `acquisition.frames_per_cube` = 1000 - see #36), `subarray_panel.py:31,33,211,213` `4096`/`2304` (should come from `IMAGE WIDTH`/`IMAGE HEIGHT` max or the params dict), `app.py:430` `1000` ms (config `gui.status_update_interval_ms`).

### G72 - LOW - Readout combobox never reflects the camera

`camera_controls.py:33` default "Ultra Quiet"; nothing maps `cam_state.params['READOUT SPEED']` back to `readout_var`. After a reconnect or a change through another path the control lies.

### G73 - LOW - Truthiness formatting

`status_bar.py:105` `if state.telescope_focus` and `telescope_settings_window.py:180` `if state.telescope_airmass` show `--` for a legitimate `0.0`. Use `is not None`.

### G74 - LOW - Close path omissions

`app.py:504-519`: iterates a stale deep copy (`cam_state` captured before `stop_streaming`, which already stops saving) and then calls `stop_saving` again; never calls `api.disconnect_gps()`; does not abort a running focus loop (`api.abort_focus_loop()`) nor stop guiding; the camera panels' stream timers are not cancelled (#44).

### G75 / G76 - LOW - Subarray panel details

`subarray_panel.py:161-164` floors to a multiple of 4 while the label at `:104` says "nearest"; no check that `hpos + hsize <= width`, `hsize >= 16`, or non-negative values. `_on_reset` (`:203-217`) calls `_on_enable_toggle()` unconditionally, so Reset when already OFF still stops/restarts streaming and issues `SUBARRAY_MODE=1.0` plus a full poll.

### G77 - LOW - Dead code (see section 4)

### G78 - LOW - Fonts

`app.py:229,236,241-245` hard-code family "Ubuntu" (silently falls back elsewhere); hint labels in `camera_controls.py:147-148`, `subarray_panel.py:103-111`, `image_display.py:170-173, 246-249` use `('TkDefaultFont', 9)` against a 14 pt base.

---

## 3. Previously reported GUI items: status

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 8 | `progress_var.set` from thread in `focus_panel.py` | VERIFIED-STILL-PRESENT (in dead code) | `gui/panels/focus_panel.py:234-250` still direct; file is not used by `app.py` (only exported from `panels/__init__.py:12`) |
| 9 | Same in `focus_window.py` | VERIFIED-STILL-PRESENT (partially fixed) | Real path `focus_window.py:498-514` direct `.set()`; sim path `:580-625` uses `after()` |
| 20 | No notification on filter failure | VERIFIED-STILL-PRESENT (partially) | `camera_controls.py:406-412` logs only; combobox does self-correct on the next 1 Hz poll (`:582-583`), with the flicker described in G68 |
| 21 | Focus thread not joined on window close | VERIFIED-STILL-PRESENT | No `destroy()` override anywhere in `gui/` (`grep "def destroy" gui/` -> none); see G62 |
| 22 | Same for `focus_panel.py` | VERIFIED-STILL-PRESENT (dead code) | `focus_panel.py` has no `destroy()` |
| 38 | `FocusPanel` unused | VERIFIED-STILL-PRESENT | `app.py` imports only `FocusWindow` (`:23`); `FocusPanel` only in `panels/__init__.py` |
| 39 | Hard-coded plate scale in focus window | APPEARS-FIXED (moved) | No plate-scale literal in `focus_window.py`; `image_display.py:94` reads `config.instrument.plate_scale_arcsec_per_pixel` |
| 40 | Photometry division guard | APPEARS-FIXED (minimal) | `image_display.py:1186` `comp_flux > 0`; no lower bound on SNR, and `:1308` still divides by `mean_rel` (G69) |
| 41 | Stale filter-list check | APPEARS-FIXED | `focus_window.py:699-706` compares against `_last_filters` and copies |
| 43 | Bare `except` in MockCamera | VERIFIED-STILL-PRESENT | `focus_window.py:146-147` bare `except: pass` |
| 44 | `after()` callbacks not cancelled on shutdown | PARTIALLY-FIXED | `image_display.py:1043-1054` cancels its loop; `camera_controls.py` stream timer (`:520`) and `app.py` timers (`:311, :430`) are not cancelled; the root cause (API notifications after `root.destroy()`, G57) remains |
| 45 | N-frames stale copy | VERIFIED-STILL-PRESENT | `camera_controls.py:281,328`; G58 |
| 46 | GUI blocks on DCAM reads during `update_status` | VERIFIED-STILL-PRESENT | `app.py:425` on the Tk thread; G55 |
| 36 | frames_per_cube default mismatch (touches GUI) | VERIFIED-STILL-PRESENT | GUI default "100" (`camera_controls.py:50`), `config.py:95` 100, `config.json:85` 1000 |

Items 1-7, 10-19, 23-35, 37, 42, 47-49 are outside GUI scope and were not re-verified here.

---

## 4. Dead code

Evidence: `grep -rn "OutputControlsPanel\|CameraSettingsPanel\|FilterPanel\|TelescopePanel\|FocusPanel" --include=*.py .` (excluding `gui_qt/`) matches only the defining files and `gui/panels/__init__.py`. `gui/app.py` imports exactly `CameraControlsPanel, SubarrayPanel, ImageDisplayPanel, StatusBar` (`app.py:17-22`) plus the three `*_window.py` modules.

| File | Status | Notes |
|------|--------|-------|
| `gui/panels/output_controls.py` (`OutputControlsPanel`) | DEAD | Exported from `panels/__init__.py:10`, never instantiated; superseded by save controls inside `CameraControlsPanel` |
| `gui/panels/camera_settings.py` (`CameraSettingsPanel`) | DEAD | Not even exported; superseded by `gui/camera_settings_window.py` |
| `gui/panels/filter_panel.py` (`FilterPanel`) | DEAD | Exported `:11`, never used; filter combobox lives in `CameraControlsPanel` |
| `gui/panels/telescope_panel.py` (`TelescopePanel`) | DEAD | Not exported; superseded by `gui/telescope_settings_window.py` |
| `gui/panels/focus_panel.py` (`FocusPanel`) | DEAD | Exported `:12`; superseded by `FocusWindow` (#38). Still imports `get_filter_exposure_multiplier` from `focus_window` |
| `gui/app.py:479-487` `_auto_connect_filterwheel` | DEAD | "legacy method", no callers |
| `gui/app.py:189-214` | DEAD | Commented-out old `_configure_style` |
| `gui/panels/image_display.py:50, 800-804, 892` `_display_lock` | DEAD LOGIC | Only ever acquired from the Tk thread inside a serialised `after` chain; cannot be contended |
| `gui/panels/image_display.py:32,36` `display_size` | UNUSED | Parameter stored, never read |
| `gui/panels/subarray_panel.py:219-227`, `image_display.py:993-995` `update_from_state` | NO-OP | Called every tick, do nothing (subarray panel therefore never reflects actual camera ROI) |
| `gui/focus_window.py:34-170` mock hardware | DUPLICATE | Near-verbatim copy of `focusloop/test_focus_loop.py:75-288` |
| `api/state.py` backward-compat properties | PARTLY USED | GUI uses `camera_connected`, `camera_streaming` (focus window / API); the rest are API-internal |

---

## 5. Checklist for the Qt port

### 5a. Behaviours that MUST be preserved (user-facing contract)

**Application / layout**
- One tab per camera (`enumerate_cameras()` order; tab text = DCAM CAMERAID, e.g. PHX2); window title `"Cerberus High-Speed Imager - PHX2, PHX3"`.
- Left column: per-camera tab (Camera Controls, Subarray, Live View), then "Camera Settings", "Telescope Settings", "Focus Settings" buttons, then a status bar with one line per camera (`"<ID>: Disconnected | Connected | Streaming | Saving (n)"` + `" 12.3C"` temperature; colours gray/blue/green) and a row `TCS: 26.2mm | Filter: r | GPS: ●/○`.
- Startup: 500 ms after the window appears, auto-connect in the background in this order: telescope (only if `config.telescope.auto_connect`), every camera, filter wheel, GPS. The GUI must stay responsive during this.
- 1 Hz status refresh (`config.gui.status_update_interval_ms`) driving temperature, exposure echo, TCS position/focus, filter, frame counters and writer stats.
- Quit: confirmation dialog; on confirm stop streaming/saving on every camera, disconnect cameras, telescope, filter wheel (add GPS), close display windows and plots, then `Dcamapi.uninit()`.
- `--sim` flag enables the simulation section in the focus window.

**Camera Controls panel**
- Connect/Disconnect button with status text ("Connecting..." orange, "Connected" green, "Failed" red, "Disconnected"); Start button disabled until connected; connect runs in the background.
- Target (default from `config.gui.default_object_name`), Comment, Filter combobox (read-only, values = `state.available_filters`, selection = `state.current_filter`; changing it moves the wheel **and** applies the calibrated focus for that filter via the API).
- Exposure entry + unit combobox `ms | s | min` (default 100 ms); `<Return>` applies; unit change converts the displayed number without touching the camera; display echoes the camera's actual exposure (formatted: integers without decimals, >=1 with <=2 decimals, <1 with `%.6g`).
- N Frames entry (empty = continuous); when set, streaming auto-stops after N frames and shows "Taking: n / N" / "Done: n images" (make it visible this time, see G58).
- Readout combobox `Ultra Quiet (READOUT_SPEED=1.0) | Standard (2.0)`.
- Save checkbox: if ticked at Start, saving begins with the stream; ticking while streaming starts saving immediately; unticking stops saving; shows a visual "actively saving" state; auto-unticks when `start_saving` fails; creates the output dir if missing (error dialog if it cannot).
- Start/Stop toggle: green "Start" / red "Stop". On Start: `start_streaming(align_to_second=True)`, start the "Streaming: HHh MMm SSs" timer, **auto-open the live view window next to the main window**, arm N-frames, start saving if ticked. On Stop: stop streaming (which stops saving after a 200 ms grace), stop the timer, **disable guiding**, clear N-frames state.
- Save Path (default `config.paths.default_output_dir`, with directory browser) and Cube Size (frames per cube, default `config.acquisition.frames_per_cube`).
- Stats: Frames captured, Saved, Cubes, Drop (red when > 0).
- Observing-night folder layout produced through the API: `<save path>/captures_YYYY_MM_DD/<CAMERAID>/`, where the date rolls over at **local noon** (`observing_night_str()`); FITS headers carry OBJECT, COMMENT, CAMERAID, filter and cached telescope data.

**Subarray (ROI) panel**
- Enable checkbox gating HPOS/HSIZE/VPOS/VSIZE entries and Apply; "Reset to Full Frame" always enabled.
- All values coerced to multiples of 4; stop -> apply -> restart streaming automatically when the camera is streaming (keep the restart, fix the save/guiding handling: G53).
- Hint texts: "Values rounded to nearest 4", "SHIFT+drag on display to select ROI".
- Reset also resets the display panel's ROI offset to (0,0).

**Live View**
- Separate resizable window per camera titled `"Cerberus Live View - <ID>"`; opens automatically on Start, manually via "Open Display"/"Close Display"; `q` or `Esc` in the display closes it.
- Rendering: 16-bit -> 8-bit with Min/Max entries (defaults 200/300) or Auto (min/max of an 8x-subsampled frame); frames smaller than 512 px on a side are upscaled with nearest-neighbour so the short side is >= 512; **horizontal flip** for north-up/east-left; overlays drawn in the flipped frame; mouse coordinates un-flipped and un-scaled back to sensor pixels and clamped.
- Readouts: FPS, Mean, Max (8x-subsampled, refreshed ~1 Hz), Cursor pixel value (live on mouse move), FWHM value.
- **Mouse gestures on the display**: SHIFT+left-drag = ROI selection (green rectangle with `WxH` label, minimum 16x16, right-click during drag cancels, result rounded to 4 and offset by the current subarray origin, applied through the subarray panel); right-click = set FWHM target (rejected within half a box of the edge); CTRL+left-click = photometry target aperture; ALT+left-click = comparison aperture (both only when Photometry is enabled).
- FWHM: 2-D Gaussian fit in a box of `fwhm_box_size_arcsec` (spinbox 1-10", step 0.5, default `config.instrument.fwhm_box_size_arcsec`) converted with the plate scale to an even pixel count; background = median of box edge; FWHM = 2.355 x sqrt(sigma_x sigma_y) x plate scale; sanity limits 1 px .. box size; magenta circle + crosshair + "F" + value overlay; "fit err" on failure; **box follows the star** (keep, but fix G51); Clear button; Plot button opens a live-updating (500 ms) FWHM-vs-time window with mean/std/min/max in the title; history capped at 1000 points, cleared on new target.
- Guiding: Enable checkbox (requires an FWHM target and a connected telescope, otherwise shows the reason), Reset (re-calibrate reference), status line (`Calibrating (n/3)... | Guiding active | Drift: x" (+dx, +dy)`), engine parameters from `config.guiding` (15 s window, 0.1" threshold, 5" max, 5 s interval, gain 0.8, sign flips), corrections through `api.move_offset(ra, dec)`; guiding auto-disabled on Stop.
- Photometry: Enable checkbox, T/C coordinate readouts, Clear, Plot (two-panel live lightcurve: raw target/comparison flux and T/C ratio with mean/std/percent in the title), aperture radius (3-50, default 20), annulus inner/outer (default 30-45); background = median of annulus; flux = sum(aperture) - background x n; 20 Hz rate limit; 10 000-point buffer; green T / cyan C circles with "T"/"C" labels.
- Display loop keeps running (slower) when the tab is hidden; Tk widgets only refreshed when visible.

**Camera Settings window**
- Camera selector (defaults to the current tab), connection label, comboboxes: Binning 1x1/2x2/4x4 (BINNING 1/2/4), Sensor Mode Standard/Photon Number (SENSOR_MODE 1.0/12.0), Trigger Source Internal/External/Software (1/2/3), Trigger Mode Normal/Start (1.0/6.0), Defect Correction OFF/ON (1/2), Hot Pixel Level STANDARD/MINIMUM/AGGRESSIVE (1/2/3); a scrollable "All Camera Parameters" table sorted by name, refreshed from `cam_state.params` (DCAM names with spaces, enum values as text).

**Telescope Settings window**
- Connect TCS / Disconnect (connect in background), RA/Dec/HA/LST/Airmass/UTC readouts, RA/Dec offset entries in arcsec + "Move Offset".

**Focus window**
- Manual: current focus (mm), Go to <mm>, Offset <mm> with -/+ buttons (each move in the background, TCS blocks until done).
- Range Start/End/Step (mm), Base exposure (s) with per-filter multipliers from `config.focusloop.exposure_multipliers` (case-insensitive), filter checkboxes populated from the wheel (all ticked by default), Status line, Run/Abort/Close; Run requires camera + telescope connected and no streaming; multi-filter runs; results summarised ("Done: 26.20mm, FWHM=0.62\"" or "Done: k/n filters successful"); best positions written back to config by the API.
- Simulation mode (only with `--sim`): checkbox, "Optimal Focus" entry, mock telescope/camera, real filter wheel if connected, output in `/tmp/cerberus_focus_sim_<date>`.
- Focus images stored under `<output>/captures_<night>/<CAMERAID>/focus/` (make the root follow config/Save Path: G63).

### 5b. Bugs that must NOT be carried over

1. **Any Tk/Qt call from a non-GUI thread** (G50, #9, #8). Use `pyqtSignal` for every API -> GUI notification; never `QTimer.singleShot` from a Python thread (present in `gui_qt/app.py:233`).
2. **API callbacks under `_callback_lock`** (G50). Fix in the API regardless of GUI toolkit: snapshot the list, call outside the lock, and make callbacks non-blocking by contract.
3. **Hardware calls on the GUI thread** (G55, #46, G70): start (alignment sleep), stop (sleeps + 60 s join), connect/disconnect (5 s join), `set_camera_property` (double full poll), `update_status`, `move_offset`, filter/focus moves. One hardware worker; GUI enqueues and reads state.
4. **Guiding fed with deltas** (G51) - pass absolute centroids.
5. **Polling-based N-frames** with stale counters and an unbound progress label (G58, #45) - implement in the API/camera thread.
6. **Subarray stop/restart that forgets saving, guiding, N-frames, and the display offset** (G53, G59, G60); check every `set_property` result; set MODE last; read the actual ROI back from params.
7. **Skipping model updates for hidden tabs** (G54).
8. **`_closing` set before the confirmation** (G52); no reschedule-less early returns in timers.
9. **Uncancellable daemon threads** and callback never unregistered before teardown (G57, #44, #21/#22): keep handles, cancel flags, join with timeout, disconnect signals, then `Dcamapi.uninit()`.
10. **`matplotlib.use()` at runtime / `pyplot` global state** (G56, G69): embed canvases (`FigureCanvasQTAgg` or pyqtgraph), keep animation references per window, and strip `matplotlib.use('Agg')` from the analyzer.
11. **`cv2.imshow` + `waitKey` for the live view.** With PyQt6 in-process, opencv-python's bundled Qt5 highgui will conflict; render into a `QLabel`/`QGraphicsView`/pyqtgraph `ImageItem` and do mouse handling in Qt (keeps the flip/scale math in one place).
12. **Silent `except: pass`** in the display loop, filter change, unit change (G65, G67).
13. **Undefined ttk style for the saving indicator; Save checkbox never synced to `is_saving`** (G61).
14. **Hard-coded paths/defaults** (`/data/cerberus`, 4096x2304, 100 frames, "Ubuntu" font, focus window defaults, `get_camera(0)`) (G63, G71, G78).
15. **Full Treeview rebuild every second** (G64) - update values in place.
16. **Display-loop FPS labelled as camera FPS** (G66).
17. **Truthiness checks on numeric state** (G73).
18. **Duplicated mock hardware** and exporting dead panels (G63, G77) - do not port `output_controls`, `camera_settings` panel, `filter_panel`, `telescope_panel`, `focus_panel`.

### 5c. UX improvements recommended for the Qt version

- Show the "Taking n/N" progress and a saving indicator (e.g. red "REC" badge with cubes/frames) directly on the Start/Stop row; disable Subarray/Camera Settings controls while saving, or ask before interrupting a save.
- Apply exposure on focus-out as well as Enter, and show the camera's quantised value next to the entry rather than overwriting the user's text; validate against the DCAM min/max.
- Replace the filter combobox flicker with a "moving..." state driven by the API (emit a `filter_moving` state), and show an error toast when a move fails.
- Make the status poll asynchronous (worker thread, 1 Hz) and show a stale indicator if a poll takes > 2 s (long exposures).
- Subarray panel should display the *actual* ROI from `cam_state.params` and greying when it differs from the entries; a "Full frame" readout of the sensor size from `IMAGE WIDTH/HEIGHT` limits.
- Live view: draw overlays in Qt (no BGR conversion per frame), add a colour-map/gamma option, a 1:1 / fit toggle, and show both camera fps and display fps.
- Guiding: expose the current reference and the last correction time in the status; add a "pause" state that keeps the reference; log corrections to the FITS COMMENT stream.
- Focus window: show the multipliers from config, prefill from `config.focusloop`, disable Run when the API reports `focus_loop_running`, and keep Abort functional for the simulation path by driving the sim loop through the API too.
- Camera settings: group DCAM parameters by prefix with a filter box; update values in place; grey out read-only properties.
- Single shutdown sequence with a progress dialog ("Stopping writer... 3 cubes pending") instead of a frozen window during the 60 s writer join.
