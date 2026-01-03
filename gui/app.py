# gui/app.py
"""Main Cerberus GUI application."""

import logging
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

from ..api import CerberusAPI
from .panels import (
    CameraControlsPanel,
    ImageDisplayPanel,
    OutputControlsPanel,
    TelescopePanel,
    FilterPanel,
    StatusBar,
)

logger = logging.getLogger(__name__)


class CerberusGUI:
    """
    Main GUI application for the Cerberus high-speed imager.

    Creates a Tkinter window with panels for camera control,
    image display, telescope control, and data acquisition.

    Example usage:
        gui = CerberusGUI()
        gui.run()
    """

    def __init__(self, title: str = "Cerberus High-Speed Imager"):
        """
        Initialize the GUI.

        Args:
            title: Window title
        """
        self.title = title

        # Create API
        self.api = CerberusAPI()

        # Create root window
        self.root = tk.Tk()
        self.root.title(title)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Configure style
        self._configure_style()

        # Create panels
        self._create_layout()

        # Register status callback
        self.api.on_status_change(self._on_status_change)

        # Start status update timer
        self._update_status()

    def _configure_style(self):
        """Configure ttk style."""
        style = ttk.Style()

        # Try to use a modern theme if available
        available_themes = style.theme_names()
        if 'clam' in available_themes:
            style.theme_use('clam')
        elif 'alt' in available_themes:
            style.theme_use('alt')

    def _create_layout(self):
        """Create the main layout with panels."""
        # Main container
        main_frame = ttk.Frame(self.root, padding=5)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Left column - Controls
        left_frame = ttk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        # Camera controls
        self.camera_panel = CameraControlsPanel(left_frame, self.api)
        self.camera_panel.pack(fill=tk.X, pady=(0, 5))

        # Output controls
        self.output_panel = OutputControlsPanel(left_frame, self.api)
        self.output_panel.pack(fill=tk.X, pady=(0, 5))

        # Telescope panel
        self.telescope_panel = TelescopePanel(left_frame, self.api)
        self.telescope_panel.pack(fill=tk.X, pady=(0, 5))

        # Filter panel
        self.filter_panel = FilterPanel(left_frame, self.api)
        self.filter_panel.pack(fill=tk.X, pady=(0, 5))

        # Right column - Display
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Image display
        self.display_panel = ImageDisplayPanel(right_frame, self.api)
        self.display_panel.pack(fill=tk.BOTH, expand=True)

        # Status bar at bottom
        self.status_bar = StatusBar(self.root, self.api)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _on_status_change(self, state):
        """Handle status change from API."""
        # Schedule update in main thread
        self.root.after(0, lambda: self._update_panels(state))

    def _update_panels(self, state):
        """Update all panels from state."""
        try:
            self.camera_panel.update_from_state(state)
            self.output_panel.update_from_state(state)
            self.telescope_panel.update_from_state(state)
            self.filter_panel.update_from_state(state)
            self.status_bar.update_from_state(state)
        except Exception as e:
            logger.error(f"Error updating panels: {e}")

    def _update_status(self):
        """Periodic status update."""
        try:
            # Update API status (polls hardware)
            self.api.update_status()
        except Exception as e:
            logger.error(f"Error in status update: {e}")

        # Schedule next update
        self.root.after(1000, self._update_status)  # Every 1 second

    def _on_close(self):
        """Handle window close."""
        if messagebox.askokcancel("Quit", "Are you sure you want to quit?"):
            logger.info("Closing Cerberus GUI...")

            # Cleanup display
            if hasattr(self.display_panel, 'cleanup'):
                self.display_panel.cleanup()

            # Cleanup API
            try:
                if self.api.state.is_saving:
                    self.api.stop_saving()
                if self.api.state.camera_streaming:
                    self.api.stop_streaming()
                if self.api.state.camera_connected:
                    self.api.disconnect_camera()
                if self.api.state.telescope_connected:
                    self.api.disconnect_telescope()
                if self.api.state.filterwheel_connected:
                    self.api.disconnect_filterwheel()
            except Exception as e:
                logger.error(f"Error during cleanup: {e}")

            self.root.destroy()

    def run(self):
        """Start the GUI main loop."""
        logger.info("Starting Cerberus GUI")
        self.root.mainloop()


def main():
    """Main entry point for the GUI."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create and run GUI
    gui = CerberusGUI()
    gui.run()


if __name__ == "__main__":
    main()
