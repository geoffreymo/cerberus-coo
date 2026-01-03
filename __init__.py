"""
Cerberus COO - High-Speed Imaging System Control

This package provides a complete interface for controlling the Cerberus
high-speed imaging system, including:

- Camera control (Hamamatsu qCMOS via DCAM)
- Telescope control (P200 via TCS)
- Filter wheel control
- Data acquisition (FITS cubes)
- Automated focus loops
- GUI interface

Quick Start:
    # Using the API directly
    from cerberus_coo.api import CerberusAPI

    with CerberusAPI() as api:
        api.connect_camera()
        api.set_exposure(0.1)
        api.start_streaming()
        api.start_saving("M31", "/data/tonight")
        time.sleep(300)
        api.stop_streaming()

    # Or run the GUI
    from cerberus_coo.gui import CerberusGUI
    gui = CerberusGUI()
    gui.run()
"""

__version__ = "1.0.0"
__author__ = "Cerberus Team"

# Core API
from .api import CerberusAPI, SystemState

# GUI
from .gui import CerberusGUI

__all__ = [
    'CerberusAPI',
    'SystemState',
    'CerberusGUI',
]
