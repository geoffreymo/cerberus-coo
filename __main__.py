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

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Load config
    from .config import load_config, get_config
    if args.config:
        load_config(args.config)
    config = get_config()

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
    from .gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
