# Cerberus COO - Claude Code Instructions

## Commit Practices

- **Commit frequently**: After completing each logical unit of work (feature, bug fix, refactor), offer to commit the changes.
- **Don't batch commits**: Small, focused commits are preferred over large commits with many unrelated changes.
- **Commit message format**: Use conventional commits style (e.g., "Add GPS auto-connect to GUI startup", "Fix timestamp rollover handling").

## Project Structure

- `api/` - CerberusAPI and system state management
- `hardware/` - Device controllers (camera, telescope, GPS, filterwheel)
- `acquisition/` - Frame capture and FITS writing (save_thread.py)
- `gui/` - Tkinter GUI panels and windows
- `gui_qt/` - PyQt6 GUI (alternative)
- `focusloop/` - Automated focus routines
- `config.json` - Runtime configuration

## Key Patterns

- Camera operations must happen on the camera thread (DCAM thread affinity)
- GPS device is shared across all cameras (single Meinberg UCAP buffer)
- Save thread uses 4-tuple: (frame, timestamp, framestamp, gps_unix)
- Config is loaded via `get_config()` from config.py

## Testing Hardware

- Meinberg GPS: `python hardware/gps_timing.py` (standalone test)
- Camera: Connect via GUI or API
- `mbggpscap` tool conflicts with Python GPS code - don't run simultaneously
