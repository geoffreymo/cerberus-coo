#!/usr/bin/env python3
"""
Cerberus High-Speed Imager - Main Entry Point

Launch the GUI:
    python -m cerberus_coo

Or from the cerberus directory:
    python -m cerberus_coo
"""

import sys
import argparse
import logging


def main():
    parser = argparse.ArgumentParser(
        description="Cerberus High-Speed Imager Control System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m cerberus_coo              # Launch GUI
    python -m cerberus_coo --sim        # Launch GUI with focus loop simulation mode
    python -m cerberus_coo --no-gui     # Print config and exit (for testing)
    python -m cerberus_coo --verbose    # Launch GUI with debug logging
        """
    )
    parser.add_argument(
        '--no-gui',
        action='store_true',
        help='Print configuration and exit (useful for testing imports)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable debug logging'
    )
    parser.add_argument(
        '--config',
        type=str,
        default=None,
        help='Path to config.json (uses default if not specified)'
    )
    parser.add_argument(
        '--sim', '--simulate',
        action='store_true',
        help='Enable simulation mode for focus loop testing (simulates telescope/camera)'
    )

    args = parser.parse_args()

    # Setup console logging first
    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=log_level,
        format=log_format
    )

    # Load config
    from .config import load_config, get_config
    import os
    from datetime import datetime
    if args.config:
        load_config(args.config)
    config = get_config()

    # Add file logging
    log_dir = config.paths.log_dir
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_filename = datetime.now().strftime('cerberus_%Y%m%d_%H%M%S.log')
            log_path = os.path.join(log_dir, log_filename)
            file_handler = logging.FileHandler(log_path)
            file_handler.setLevel(log_level)
            file_handler.setFormatter(logging.Formatter(log_format))
            logging.getLogger().addHandler(file_handler)
            logging.info(f"Logging to {log_path}")
        except Exception as e:
            logging.warning(f"Could not create log file in {log_dir}: {e}")

    if args.no_gui:
        # Just print config and exit
        print("Cerberus Configuration:")
        print(f"  Telescope: {config.telescope.host}:{config.telescope.port}")
        print(f"  Filters: {list(config.filterwheel.filters.values())}")
        print(f"  Frames per cube: {config.acquisition.frames_per_cube}")
        print(f"  Output dir: {config.paths.default_output_dir}")
        print("\nConfig loaded successfully!")
        return 0

    # Launch GUI
    from .gui.app import CerberusGUI
    gui = CerberusGUI(enable_simulation=args.sim)
    gui.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
