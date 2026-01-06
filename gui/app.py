# gui/app.py
"""Main Cerberus GUI application."""

import os
import logging
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

# Limit NumPy threading to reduce CPU usage during display (like v18 GUI)
os.environ['OMP_NUM_THREADS'] = '4'
os.environ['MKL_NUM_THREADS'] = '4'
os.environ['NUMEXPR_NUM_THREADS'] = '4'

from ..api import CerberusAPI
from .panels import (
    CameraControlsPanel,
    CameraSettingsPanel,
    SubarrayPanel,
    ImageDisplayPanel,
    TelescopePanel,
    StatusBar,
)
from .focus_window import FocusWindow

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

        # Focus window reference
        self._focus_window = None

        # Closing flag to prevent updates during shutdown
        self._closing = False

        # Start status update timer
        self._update_status()

        # Auto-connect hardware after GUI is ready
        self.root.after(500, self._auto_connect_hardware)

    # def _configure_style(self):
    #     """Configure ttk style."""
    #     style = ttk.Style()

    #     # Try to use a modern theme if available
    #     available_themes = style.theme_names()
    #     if 'clam' in available_themes:
    #         style.theme_use('clam')
    #     elif 'alt' in available_themes:
    #         style.theme_use('alt')

    #     # Configure larger fonts for better readability
    #     default_font = ('TkDefaultFont', 12)
    #     heading_font = ('TkDefaultFont', 14, 'bold')

    #     style.configure('.', font=default_font)
    #     style.configure('TLabel', font=default_font)
    #     style.configure('TButton', font=default_font)
    #     style.configure('TEntry', font=default_font)
    #     style.configure('TCombobox', font=default_font)
    #     style.configure('TLabelframe.Label', font=heading_font)
    #     style.configure('TCheckbutton', font=default_font)
    #     style.configure('TRadiobutton', font=default_font)

    #     # Also set root window default font for tk widgets
    #     self.root.option_add('*Font', default_font)
    def _configure_style(self):
        """Configure Tk / ttk style in a minimal, sane way."""
        from tkinter import font
        from tkinter import ttk

        style = ttk.Style()

        # Use a decent base theme (clam is safest if not using ttkbootstrap)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        # Modify the named default font IN PLACE
        default_font = font.nametofont("TkDefaultFont")
        default_font.configure(
            family="Ubuntu",  # or "Noto Sans"
            size=16
        )

        # Make headings slightly heavier via a named style only
        style.configure(
            "TLabelframe.Label",
            font=(default_font.actual("family"), 16, "normal")
        )


    def _create_layout(self):
        """Create the main layout with panels."""
        # Main container
        main_frame = ttk.Frame(self.root, padding=5)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Left side - Controls in two columns
        controls_frame = ttk.Frame(main_frame)
        controls_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        # Left column 1 - Camera and Telescope
        col1_frame = ttk.Frame(controls_frame)
        col1_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        # Camera controls (includes save and filter selection)
        self.camera_panel = CameraControlsPanel(col1_frame, self.api)
        self.camera_panel.pack(fill=tk.X, pady=(0, 5))

        # Telescope panel
        self.telescope_panel = TelescopePanel(col1_frame, self.api)
        self.telescope_panel.pack(fill=tk.X, pady=(0, 5))

        # Left column 2 - Camera Settings, Subarray, Focus
        col2_frame = ttk.Frame(controls_frame)
        col2_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        # Camera settings
        self.settings_panel = CameraSettingsPanel(col2_frame, self.api)
        self.settings_panel.pack(fill=tk.X, pady=(0, 5))

        # Subarray controls
        self.subarray_panel = SubarrayPanel(col2_frame, self.api)
        self.subarray_panel.pack(fill=tk.X, pady=(0, 5))

        # Focus button
        focus_btn_frame = ttk.Frame(col2_frame)
        focus_btn_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(
            focus_btn_frame, text="Open Focus", command=self._open_focus_window
        ).pack(fill=tk.X, padx=5, pady=5)

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
        # Skip updates if closing
        if self._closing:
            return

        try:
            self.camera_panel.update_from_state(state)
            self.settings_panel.update_from_state(state)
            self.subarray_panel.update_from_state(state)
            self.telescope_panel.update_from_state(state)
            self.status_bar.update_from_state(state)

            # Update focus window if it's open
            if hasattr(self, '_focus_window') and self._focus_window and self._focus_window.winfo_exists():
                self._focus_window.update_from_state(state)
        except Exception as e:
            # Ignore 'popdown' errors when combobox dropdowns are open
            err_str = str(e).lower()
            if 'popdown' not in err_str:
                if not self._closing:
                    logger.error(f"Error updating panels: {e}")

    def _open_focus_window(self):
        """Open the focus loop window."""
        # If window already exists and is open, just raise it
        if self._focus_window and self._focus_window.winfo_exists():
            self._focus_window.lift()
            self._focus_window.focus()
            return

        # Create new focus window
        self._focus_window = FocusWindow(self.root, self.api)
        self._focus_window.update_from_state(self.api.state)

    def _update_status(self):
        """Periodic status update."""
        # Stop updates if closing
        if self._closing:
            return

        try:
            # Update API status (polls hardware)
            self.api.update_status()
        except Exception as e:
            logger.error(f"Error in status update: {e}")

        # Schedule next update
        self.root.after(1000, self._update_status)  # Every 1 second

    def _auto_connect_hardware(self):
        """Attempt to auto-connect hardware on startup (in background)."""
        import threading

        def connect_thread():
            # Auto-connect camera
            try:
                if self.api.connect_camera():
                    logger.info("Camera auto-connected")
                else:
                    logger.info("Camera not available for auto-connect")
            except Exception as e:
                logger.debug(f"Camera auto-connect failed: {e}")

            # Auto-connect filterwheel
            try:
                if self.api.connect_filterwheel():
                    logger.info("Filter wheel auto-connected")
                else:
                    logger.info("Filter wheel not available for auto-connect")
            except Exception as e:
                logger.debug(f"Filter wheel auto-connect failed: {e}")

        threading.Thread(target=connect_thread, daemon=True).start()

    def _auto_connect_filterwheel(self):
        """Attempt to auto-connect filterwheel on startup (legacy method)."""
        try:
            if self.api.connect_filterwheel():
                logger.info("Filter wheel auto-connected")
            else:
                logger.info("Filter wheel not available for auto-connect")
        except Exception as e:
            logger.debug(f"Filter wheel auto-connect failed: {e}")

    def _on_close(self):
        """Handle window close."""
        # Set closing flag to stop updates
        self._closing = True

        if messagebox.askokcancel("Quit", "Are you sure you want to quit?"):
            logger.info("Closing Cerberus GUI...")

            # Cleanup display
            if hasattr(self.display_panel, 'cleanup'):
                self.display_panel.cleanup()

            # Cleanup API
            try:
                # Stop streaming first to prevent new frames
                if self.api.state.camera_streaming:
                    self.api.stop_streaming()
                # Brief pause for in-flight frames
                if self.api.state.is_saving:
                    import time
                    time.sleep(0.05)
                    self.api.stop_saving()
                if self.api.state.camera_connected:
                    self.api.disconnect_camera()
                if self.api.state.telescope_connected:
                    self.api.disconnect_telescope()
                if self.api.state.filterwheel_connected:
                    self.api.disconnect_filterwheel()
            except Exception as e:
                logger.error(f"Error during cleanup: {e}")

            self.root.destroy()
        else:
            # User cancelled, resume updates
            self._closing = False

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
