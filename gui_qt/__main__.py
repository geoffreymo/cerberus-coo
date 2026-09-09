#!/usr/bin/env python3
"""
Cerberus PyQt6 GUI entry point.

    python -m cerberus_coo.gui_qt            # real hardware (all connected cameras)
    python -m cerberus_coo.gui_qt --sim      # simulated cameras/telescope/filter wheel/GPS
    python -m cerberus_coo.gui_qt --list-cameras
"""

import argparse
import logging
import os
import sys
from datetime import datetime


def _parse_size(text: str):
    w, h = text.lower().split("x")
    return int(w), int(h)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cerberus_coo.gui_qt",
                                     description="Cerberus High-Speed Imager - PyQt6 GUI")
    parser.add_argument('--sim', '--simulate', action='store_true',
                        help='Run with simulated hardware (no DCAM/TCS/filter wheel needed)')
    parser.add_argument('--sim-cameras', type=int, default=2, help='Number of simulated cameras (default 2)')
    parser.add_argument('--sim-size', type=_parse_size, default=(4096, 2304),
                        help='Simulated sensor size WxH (default 4096x2304)')
    parser.add_argument('--no-autoconnect', action='store_true', help='Do not auto-connect hardware at startup')
    parser.add_argument('--verbose', '-v', action='store_true', help='Debug logging')
    parser.add_argument('--config', type=str, default=None, help='Path to config.json')
    parser.add_argument('--light', action='store_true', help='Use the light theme')
    parser.add_argument('--font-size', type=int, default=12, help='Base font size in points (default 12; Ctrl+= / Ctrl+- at runtime)')
    parser.add_argument('--list-cameras', action='store_true', help='List DCAM cameras and exit')
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=log_level, format=log_format)

    from ..config import load_config, get_config
    if args.config:
        load_config(args.config)
    config = get_config()
    config_save_path = args.config  # save back to the file we loaded, not the package default

    dcam_initialised = False
    if args.sim:
        from ..simulation import make_simulated_api, SIM_DATA_DIR
        os.makedirs(SIM_DATA_DIR, exist_ok=True)
        config.paths.default_output_dir = SIM_DATA_DIR
        config.paths.log_dir = os.path.join(SIM_DATA_DIR, "logs")
        api = make_simulated_api(n_cameras=args.sim_cameras, sensor_size=args.sim_size)
        cameras = api.get_cameras()
        logging.info(f"SIMULATION MODE: {len(cameras)} camera(s) {args.sim_size[0]}x{args.sim_size[1]}, "
                     f"data in {SIM_DATA_DIR}")
    else:
        from ..hardware.dcam import Dcamapi, Dcam, DCAM_IDSTR
        if not Dcamapi.init():
            print("Failed to initialize DCAM API (no camera hardware?). Use --sim to run without hardware.")
            return 1
        dcam_initialised = True
        count = Dcamapi.get_devicecount()
        cameras = []
        for i in range(count):
            dcam = Dcam(i)
            cameras.append((i, dcam.dev_getstring(DCAM_IDSTR.CAMERAID)))
        if args.list_cameras:
            if not cameras:
                print("No cameras found")
            for i, cid in cameras:
                print(f"  #{i}: CAMERAID={cid}")
            Dcamapi.uninit()
            return 0
        if not cameras:
            print("No cameras found. Use --sim to run without hardware.")
            Dcamapi.uninit()
            return 1
        from ..api import CerberusAPI
        api = CerberusAPI(cameras=cameras)
        if config_save_path:
            api.config_save_path = config_save_path
        logging.info(f"Found {len(cameras)} camera(s): {[c[1] for c in cameras]}")

    # File logging
    log_dir = config.paths.log_dir
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, datetime.now().strftime('cerberus_qt_%Y%m%d_%H%M%S.log'))
            fh = logging.FileHandler(log_path)
            fh.setLevel(log_level)
            fh.setFormatter(logging.Formatter(log_format))
            logging.getLogger().addHandler(fh)
            logging.info(f"Logging to {log_path}")
        except Exception as e:
            logging.warning(f"Could not create log file in {log_dir}: {e}")

    from PyQt6.QtWidgets import QApplication
    from .theme import apply_theme
    from .app import CerberusQtGUI

    app = QApplication(sys.argv)
    app.setApplicationName("Cerberus")
    app.setOrganizationName("Cerberus")
    apply_theme(app, dark=not args.light, base_point_size=args.font_size)

    gui = CerberusQtGUI(api, cameras, enable_simulation=args.sim, auto_connect=not args.no_autoconnect,
                        dark=not args.light)
    gui.resize(780, 1180)
    gui.show()
    rc = app.exec()

    if dcam_initialised:
        Dcamapi.uninit()
    return rc


if __name__ == "__main__":
    sys.exit(main())
