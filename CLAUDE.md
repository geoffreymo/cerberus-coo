# Cerberus COO - Claude Code Instructions

## Commit Practices

- **Commit frequently**: After completing each logical unit of work (feature, bug fix, refactor), offer to commit the changes.
- **Don't batch commits**: Small, focused commits are preferred over large commits with many unrelated changes.
- **Commit message format**: Use conventional commits style (e.g., "Add GPS auto-connect to GUI startup", "Fix timestamp rollover handling").

## Project Structure

- `api/` - CerberusAPI and system state management (hardware factories allow injection of simulated devices)
- `hardware/` - Device controllers (camera, telescope, GPS, filterwheel)
- `acquisition/` - Frame capture and FITS writing (save_thread.py)
- `gui/` - Tkinter GUI (original)
- `gui_qt/` - PyQt6 GUI (full port; the version to move to). `python -m cerberus_coo.gui_qt [--sim]`
- `simulation/` - Simulated camera/telescope/filter wheel/GPS (`make_simulated_api()`)
- `focusloop/` - Automated focus routines (needs SExtractor `sex`, present in the conda env)
- `guiding/` - Autoguiding engine
- `tests/` - pytest: `test_simulation_api.py` (API, ~1 min), `test_gui_qt_sim.py` (scripted offscreen Qt session, ~3 min)
- `docs/review_2026_09_09/` + `CODE_REVIEW_2026_09_09.md` - code review findings (H/A/G numbering)
- `config.json` - Runtime configuration

## Environment

- Always use the `qcmos-control` conda env: `/home/hades/miniconda3/envs/qcmos-control/bin/python` (Python 3.12, PyQt6 6.9).
- Run Qt tests/GUI headlessly with `QT_QPA_PLATFORM=offscreen`; the real display is `:1`.

## Key Patterns

- Camera operations must happen on the camera thread (DCAM thread affinity)
- GPS device is shared across all cameras (single Meinberg UCAP buffer)
- Save thread uses 4-tuple: (frame, timestamp, framestamp, gps_unix)
- Config is loaded via `get_config()` from config.py; always use relative imports (`from ..config import ...`), never `from config import`
- API status callbacks fire on whatever thread called the API; the Qt GUI marshals them through `gui_qt/bridge.py` (Qt signals). Never touch widgets from those callbacks.
- In the Qt GUI every blocking API call goes through `gui_qt/workers.py::run_async`; the GUI thread never calls hardware directly.
- Simulation mode writes to `/tmp/cerberus_sim` and saves config to `config.sim.json` there - never to the tracked `config.json`.

## Testing Hardware

- Meinberg GPS: `python hardware/gps_timing.py` (standalone test)
- Camera: Connect via GUI or API
- `mbggpscap` tool conflicts with Python GPS code - don't run simultaneously
- No hardware attached: use `--sim` (Qt GUI) or `make_simulated_api()` (scripts/tests)
