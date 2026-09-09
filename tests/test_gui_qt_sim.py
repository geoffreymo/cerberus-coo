"""
Runs the scripted GUI session (tests/gui_qt_driver.py) in a subprocess with the
offscreen Qt platform. Takes ~3 minutes; skipped when PyQt6 is unavailable.

    pytest tests/test_gui_qt_sim.py -v
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")


def test_gui_qt_scripted_session(tmp_path):
    driver = Path(__file__).with_name("gui_qt_driver.py")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, str(driver), str(tmp_path)], env=env,
                          capture_output=True, text=True, timeout=900)
    checks = [line for line in proc.stdout.splitlines() if line.startswith("CHECK")]
    failed = [line for line in checks if "FAIL" in line]
    assert proc.returncode == 0, "driver failed:\n" + "\n".join(failed) + "\n" + proc.stderr[-3000:]
    assert len(checks) >= 45 and not failed
    assert (tmp_path / "03_liveview_streaming.png").exists()
