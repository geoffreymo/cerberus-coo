"""
PyQt6 GUI for the Cerberus High-Speed Imager.

Run with:
    python -m cerberus_coo.gui_qt          # real hardware
    python -m cerberus_coo.gui_qt --sim    # simulated hardware
"""

from .app import CerberusQtGUI
from .__main__ import main

__all__ = ['CerberusQtGUI', 'main']
