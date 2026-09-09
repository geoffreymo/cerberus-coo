# Cerberus COO

Control software for the Cerberus high-speed imager (Hamamatsu qCMOS cameras,
Palomar P200 TCS, ZWO filter wheel, Meinberg GPS timing).

```
api/           CerberusAPI - single entry point used by both GUIs and scripts
hardware/      Device controllers: camera (DCAM), telescope (haletcs), GPS timing
filterwheel/   ZWO EFW filter wheel
acquisition/   Save thread: frames -> FITS cubes (astropy)
focusloop/     Automated focus sequence + SExtractor FWHM analysis
guiding/       Autoguiding engine (pure logic, no GUI dependency)
gui/           Tkinter GUI (original)
gui_qt/        PyQt6 GUI (new; the version to move to)
simulation/    Simulated camera / telescope / filter wheel / GPS for hardware-free runs
tests/         pytest suite (API against simulated hardware, scripted Qt GUI session)
docs/          Code-review reports
config.json    Runtime configuration
```

## Environment

Everything runs in the `qcmos-control` conda environment (Python 3.12, PyQt6,
numpy, scipy, astropy, matplotlib, haletcs, SExtractor):

```bash
conda activate qcmos-control
cd ~/cerberus/cerberus-coo
```

The package is installed in editable mode as `cerberus_coo`.

## Running

Tkinter GUI (original):

```bash
python -m cerberus_coo                 # all connected cameras
python -m cerberus_coo --list-cameras
```

PyQt6 GUI:

```bash
python -m cerberus_coo.gui_qt          # real hardware
python -m cerberus_coo.gui_qt --sim    # simulated hardware, no cameras/TCS needed
python -m cerberus_coo.gui_qt --help   # --sim-cameras N, --sim-size WxH, --light, --font-size, --no-autoconnect, --config
```

`--sim` runs the *real* API, save thread, focus loop and SExtractor against a
synthetic sky: star FWHM follows the simulated telescope focus, the field drifts
slowly (guiding corrects it), one star is variable (photometry), and subarray /
binning are honoured. Simulated data goes to `/tmp/cerberus_sim`; the real
`config.json` is never written in simulation mode.

### Qt GUI quick reference

* One tab per camera; Start/Stop, Save, exposure with unit selector, N Frames
  (auto-stop), readout, filter (with calibrated focus preset), subarray/ROI.
* Live view window per camera (opens automatically on Start): wheel zoom,
  left-drag pan, **SHIFT+drag** = ROI, **right-click** = track a star (FWHM),
  **CTRL+click** / **ALT+click** = photometry target / comparison. The "Click"
  combo makes a plain click do any of these. Live FWHM-history and lightcurve
  plots with CSV export.
* Windows menu: Camera Settings (all DCAM parameters), Telescope (position,
  status, offsets/nudges), Focus (manual focus, focus loop with per-filter
  exposures, results table and focus curve). `F1` lists the shortcuts.
* Every hardware call runs on a worker thread; the 1 Hz status poll never
  blocks the UI (long exposures no longer freeze the window).

## Tests

```bash
pytest tests/test_simulation_api.py -v      # API against simulated hardware (~1 min)
pytest tests/test_gui_qt_sim.py -v          # scripted Qt session, offscreen (~3 min)
QT_QPA_PLATFORM=offscreen python tests/gui_qt_driver.py /tmp/shots   # same, with screenshots
```

## Reviews and known issues

* `BUG_REPORT.md` - running list of known bugs (#1-#49 plus the 2026-09-09 review).
* `CODE_REVIEW_2026_09_09.md` - summary of the September 2026 review: what was
  fixed, what is still open, and notes for the Qt port.
* `docs/review_2026_09_09/` - the detailed per-area reports.
* `NOTES.md` - high frame-rate limitations.
