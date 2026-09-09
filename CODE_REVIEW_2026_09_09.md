# Cerberus-COO Code Review — 2026-09-09

Fresh-eyes review of the whole control stack, done alongside the PyQt6 GUI port.
Three independent reviewers covered the hardware/acquisition layer (**H50–H70**),
the API/config/focus/guiding layer (**A50–A72**) and the Tkinter GUI (**G50–G78**).
The full reports, with line numbers, failure scenarios and suggested fixes, are in
`docs/review_2026_09_09/`. This file is the summary: what was fixed in this pass
and what is still open, in priority order. Earlier items keep their `BUG_REPORT.md`
numbers (#1–#49).

## 1. Fixed in this pass

| ID | Area | What changed |
|----|------|--------------|
| G50 / A54 | api | Status and frame callbacks are now invoked **outside** `_callback_lock` (snapshot the list first). Removes the lock-order deadlock between API notifications and the GUI, and lets callbacks (un)register themselves. |
| G51 | gui, gui_qt | Guiding was fed the star's offset from the (re-centred) box instead of its absolute position, so drift never accumulated and corrections essentially never fired. Both GUIs now pass the absolute centroid. Verified in simulation: an injected 1″ error is corrected by the configured 80 % gain. |
| A52 | api | `connect_camera` / `connect_telescope` no longer do hardware I/O while holding `_state_lock` (the camera thread takes that lock per frame). |
| A51 / A63 | api | `update_status()` no longer issues a duplicate `REQSTAT`; `set_camera_property()` refreshes only that camera's cached parameters instead of running the full hardware poll (5× per ROI change before). |
| H57 (#1) | hardware | TCS **status** socket now has its own lock (only the command socket was locked before); GUI poll and focus loop no longer interleave replies. |
| A50 | config | An unknown key in `config.json` no longer silently replaces the *entire* configuration with defaults; unknown keys are dropped with a warning. |
| (a) | api, guiding, gui | `from config import …` (absolute import that only worked when the CWD was the package directory, and created a second `config` module) replaced with relative imports in `api/cerberus.py`, `guiding/engine.py`, `gui/focus_window.py`, `gui/panels/focus_panel.py`. `start_saving()` raised `ModuleNotFoundError` when launched from elsewhere. |
| (b) / A65 | api | `_save_config()` serialises every dataclass field (`dataclasses.asdict`) — it used to drop `paths.log_dir`, `focusloop.exposure_multipliers`, `instrument.fwhm_box_size_arcsec`, `guiding.min_samples` on every focus-loop save. Dead code removed. `--config PATH` is now also the save target (Qt). Simulation mode saves to `/tmp/cerberus_sim/config.sim.json`, never the tracked file. |
| #11 / #12 | api | `set_focus` / `offset_focus` logging no longer crashes on `None`. |
| — | api | `calibrate_filter_focus()` treated the `run_focus_loop()` dict as a `FocusResult`. |
| A53 | api | Focus-loop FITS `EXPTIME` read from the controller, not the stale cached state. |
| A55 | api | `save_thread` / `save_queue` initialised in `__init__` (`is_saving()` raised `AttributeError` until the first save). |
| A56 | api | `start_saving()` on an already-saving camera is refused instead of orphaning the previous save thread. |
| A58 / G68 | api | Filter-wheel reads use a non-blocking acquire of `_filterwheel_lock` and fall back to the cached name, so the EFW library is never called from two threads and the combobox no longer flickers back to the old filter during a move. |
| A59 | api | `current_filter` takes the wheel's canonical spelling (config keys are case-sensitive). |
| A62 | api | `run_focus_loop(config=None)` builds its config from `config.json` instead of the 30–45 mm hard-coded defaults. |
| A64 | api | Transient read failures no longer overwrite good exposure/temperature/focus with `None`. |
| A69 | api | `__exit__` stops streaming (which stops saving) on **every** camera, in the right order. |
| A71 | packaging | `cerberus_coo/__init__.py` imports the Tk GUI lazily; `pyproject.toml` lists `guiding`, `gui_qt.*`, `simulation`, adds `scipy` and a `qt` extra. |
| H53 | api | `start_saving()` creates the output folder synchronously and fails fast (a dead save thread used to leave the camera feeding an orphaned 50 000-slot queue → OOM). |
| H54 / #10 | acquisition | A failed cube copy/submit no longer wedges the buffer index (every later frame was silently discarded); failed writes are counted in `total_frames_dropped`. |
| H59 / H55 (part) | hardware | `_process_frame` snapshots `save_queue` / `_gps_device` (they are cleared from other threads) so a mid-frame change can't skip `_frame_index += 1` and duplicate a frame; queue-full drops are now counted and logged. |
| H65 | hardware | Frame rate reset to 0 on stop (was reported forever). |
| G56 | focusloop | `plot_focus_curve()` builds an Agg figure directly; `matplotlib.use('Agg')` on the focus thread used to close every interactive figure the GUI had open. |
| — | focusloop | SExtractor located via `shutil.which` (`sex` / `source-extractor`) with a fallback to the interpreter's env; `FocusLoopConfig.camera_index` so focus runs use the selected camera. `capture_single_to_fits()` / `run_focus_loop()` accept `camera_index`. |
| #45 / G58 | gui (Tk) | N-frames start count reset to 0 (stale copy made auto-stop never fire after the first run). |
| G52 | gui (Tk) | `_closing` is set only after the Quit dialog is confirmed (cancelling used to stop the status poll for good). |

Everything the Tk GUI reviewer listed as "must not be carried over" (section 5b of
`docs/review_2026_09_09/tkinter_gui.md`) is addressed in `gui_qt/`: no widget access
from foreign threads (`bridge.py`), no hardware calls on the GUI thread (`workers.py`),
N-frames driven by the camera's frame callback, saving/offset/targets handled across ROI
changes, hidden tabs still updated, cancellable shutdown with a progress dialog, embedded
matplotlib canvases, Qt-native live view instead of `cv2.imshow`, config-driven defaults,
in-place parameter table updates, camera fps shown separately from display fps.

## 2. Still open — prioritised

**HIGH (data integrity, needs the real hardware to verify a fix)**

* **H50** `hardware/camera/controller.py` — ring-buffer overwrite race in the batch read: overflow is checked once per 200-frame batch with `>` instead of `>=`, a 10-frame margin after a jump, and recycled/torn slots are still queued to FITS. This is the NOTES.md 100 Hz corruption. Fix sketch in the hardware report.
* **H51 / H52 / H69** — GPS↔frame pairing is a blind FIFO pop per frame with no validation or resync (software side of #48 "pattern 2"); the ctypes `PCPS_HR_TIME` layout does not match the packed Meinberg struct (`signal` holds `utc_offs`, status flags never checked — #49 cannot be done on that field); the shared GPS device is unsafe with two streaming cameras (`clear_buffer()` discards the other camera's pulses).
* **H55** — no backpressure: the save queue is "bounded" at 950 GB of full frames, `pending_writes` is unbounded, the `acquisition.*` config knobs are unused. Only the drop counter was added.

**MEDIUM**

* **H56** force-stop path leaves DCAM capturing with the buffer allocated; **H58** non-timeout DCAM errors spin silently at 100 % CPU; **H61** DCAM calls run on whichever thread calls the controller (contradicts the single-thread design); **H62** `DATE-OBS` is the dequeue time, not exposure start; **H63** final-cube write failures are not logged; **H60** filter wheel ignores every EFW return code (unplugged wheel reports "clear").
* **A57** `stop_streaming()` → `stop_saving()` joins the writer for up to 60 s on the caller's thread (the Qt GUI runs it on a worker, the API contract is unchanged); **A60 / #13** unlocked, non-atomic `_state` reads (torn telescope headers possible); **A61** guiding history cap (100) defeats the 15 s averaging window above ~7 Hz; correction is synchronous in the Tk GUI (worker thread in Qt).
* Tk GUI only (fixed in Qt): **G53** subarray change silently ends saving; **G54** hidden tab never updates; **G55** blocking calls on the GUI thread (#46); **G57** uncancellable auto-connect thread; **G59/G60** ROI offset/ordering; **G61** undefined "saving" style; **G62** focus window threading (#9, #21, #22); **G63** hard-coded focus paths/defaults; **G64** parameter tree rebuilt every second; **G65–G70**.

**LOW**

* H64 (same-second filename collision overwrites `cube001`), H66 (hard-coded rollover thresholds), H67, H68, H70; A66 (`state.errors` write-only), A67 (hand-rolled state copy), A68 (focus-loop abort latency, `COMPLETE` overwrites `ABORTED`, no SExtractor timeout), A70 (entry-point ordering), A72 (document noon rollover); still-open older items #5, #6, #16, #18, #19, #26–#29, #31, #32, #34, #36; Tk G71–G78.

## 3. Verification

* `tests/test_simulation_api.py` — API against simulated hardware: connect, stream, FITS cube contents (headers, GPS column, consecutive framestamps), subarray/binning, filter → focus preset, telescope moves, a real SExtractor focus loop, re-entrant status callbacks.
* `tests/test_gui_qt_sim.py` — scripted PyQt6 session (50 checks, offscreen).
* The hardware-layer findings (H50–H70) could not be exercised without the cameras; they are documented with fixes but left untouched on purpose.
