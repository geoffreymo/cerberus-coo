"""
PyQt6 GUI for the Cerberus High-Speed Imager.

Run with:
    python -m cerberus_coo.gui_qt          # real hardware
    python -m cerberus_coo.gui_qt --sim    # simulated hardware
"""

from .app import CerberusQtGUI

__all__ = ['CerberusQtGUI', 'main']


def main(argv=None) -> int:
    """Entry point (imported lazily so `python -m cerberus_coo.gui_qt` doesn't double-import __main__)."""
    from .__main__ import main as _main
    return _main(argv)
