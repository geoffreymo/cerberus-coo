# gui/app.py
"""Main Cerberus GUI application."""

import os
import logging
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, List, Tuple, Dict

# Limit NumPy threading to reduce CPU usage during display (like v18 GUI)
os.environ['OMP_NUM_THREADS'] = '4'
os.environ['MKL_NUM_THREADS'] = '4'
os.environ['NUMEXPR_NUM_THREADS'] = '4'

from ..api import CerberusAPI
from ..config import get_config
from .panels import (
    CameraControlsPanel,
    SubarrayPanel,
    ImageDisplayPanel,
    StatusBar,
)
from .focus_window import FocusWindow
from .camera_settings_window import CameraSettingsWindow
from .telescope_settings_window import TelescopeSettingsWindow

logger = logging.getLogger(__name__)


class CameraTab(ttk.Frame):
    """
    A tab containing all per-camera controls.

    Each camera gets its own tab with camera controls, subarray settings,
    and display controls.
    """

    def __init__(self, parent, api: 'CerberusAPI', camera_index: int, camera_id: str):
        super().__init__(parent, padding=5)
        self.api = api
        self.camera_index = camera_index
        self.camera_id = camera_id

        self._create_widgets()

    def _create_widgets(self):
        """Create per-camera widgets."""
        # Camera controls (includes save and filter selection)
        self.camera_panel = CameraControlsPanel(self, self.api, camera_index=self.camera_index)
        self.camera_panel.pack(fill=tk.X, pady=(0, 5))

        # Subarray controls
        self.subarray_panel = SubarrayPanel(self, self.api, camera_index=self.camera_index)
        self.subarray_panel.pack(fill=tk.X, pady=(0, 5))

        # Live view controls
        self.display_panel = ImageDisplayPanel(self, self.api, camera_index=self.camera_index)
        self.display_panel.pack(fill=tk.X, pady=(0, 5))

        # Connect camera panel to display panel for auto-open on start
        self.camera_panel.set_display_panel(self.display_panel)

        # Connect display panel ROI selection to subarray panel
        self.display_panel.on_roi_selected = self._on_roi_selected

        # Connect subarray panel reset to display panel offset reset
        self.subarray_panel.on_reset = self._on_subarray_reset

    def _on_roi_selected(self, hpos: int, vpos: int, hsize: int, vsize: int):
        """Handle ROI selection from display panel (SHIFT+drag)."""
        logger.info(f"[{self.camera_id}] ROI selected: {hsize}x{vsize} at ({hpos}, {vpos})")
        self.subarray_panel.apply_roi(hpos, vpos, hsize, vsize)
        self.display_panel.set_current_subarray_offset(hpos, vpos)

    def _on_subarray_reset(self):
        """Handle subarray reset - reset display panel offset."""
        logger.info(f"[{self.camera_id}] Subarray reset to full frame")
        self.display_panel.set_current_subarray_offset(0, 0)

    def update_from_state(self, state):
        """Update all panels in this tab from system state."""
        # Get per-camera state
        cam_state = state.get_camera(self.camera_index)

        self.camera_panel.update_from_state(state, cam_state)
        self.subarray_panel.update_from_state(state, cam_state)
        # Display panel updates itself from the display loop

    def cleanup(self):
        """Cleanup resources."""
        if hasattr(self.display_panel, 'cleanup'):
            self.display_panel.cleanup()


class CerberusGUI:
    """
    Main GUI application for the Cerberus high-speed imager.

    Creates a Tkinter window with panels for camera control,
    image display, telescope control, and data acquisition.

    Example usage:
        gui = CerberusGUI()
        gui.run()
    """

    def __init__(
        self,
        title: str = "Cerberus High-Speed Imager",
        enable_simulation: bool = False,
        cameras: List[Tuple[int, str]] = None
    ):
        """
        Initialize the GUI.

        Args:
            title: Window title
            enable_simulation: Enable simulation mode in focus window
            cameras: List of (camera_index, camera_id) tuples
        """
        # Default to single camera if not specified
        if cameras is None:
            cameras = [(0, "Camera 0")]
        self.cameras = cameras

        # For backward compat, track first camera
        self.camera_index = cameras[0][0]
        self.camera_id = cameras[0][1]

        # Update title to show all cameras
        camera_ids = [c[1] for c in cameras]
        title = f"{title} - {', '.join(camera_ids)}"
        self.title = title
        self.enable_simulation = enable_simulation

        # Create API with all cameras
        self.api = CerberusAPI(cameras=cameras)

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

        # Camera settings window reference
        self._camera_settings_window = None

        # Telescope settings window reference
        self._telescope_settings_window = None

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
            size=14
        )

        # Also modify TkTextFont for Entry widgets
        text_font = font.nametofont("TkTextFont")
        text_font.configure(
            family="Ubuntu",
            size=14
        )

        # Explicitly configure Entry and Combobox fonts
        style.configure("TEntry", font=("Ubuntu", 14))
        style.configure("TCombobox", font=("Ubuntu", 14))

        # Configure Combobox dropdown list font (uses Tk option database)
        self.root.option_add("*TCombobox*Listbox.font", ("Ubuntu", 14))

        # Make headings slightly heavier via a named style only
        style.configure(
            "TLabelframe.Label",
            font=(default_font.actual("family"), 14, "normal")
        )


    def _create_layout(self):
        """Create the main layout with panels."""
        # Main container
        main_frame = ttk.Frame(self.root, padding=5)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Left side - Controls
        left_frame = ttk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        # Status bar at bottom of left pane (pack first with side=BOTTOM)
        self.status_bar = StatusBar(left_frame, self.api, cameras=self.cameras)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Settings buttons frame (anchored above status bar)
        buttons_frame = ttk.Frame(left_frame)
        buttons_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 5))

        ttk.Button(
            buttons_frame, text="Camera Settings", command=self._open_camera_settings_window
        ).pack(fill=tk.X, padx=5, pady=2)

        ttk.Button(
            buttons_frame, text="Telescope Settings", command=self._open_telescope_settings_window
        ).pack(fill=tk.X, padx=5, pady=2)

        ttk.Button(
            buttons_frame, text="Focus Settings", command=self._open_focus_window
        ).pack(fill=tk.X, padx=5, pady=2)

        # Create notebook for per-camera tabs
        self.notebook = ttk.Notebook(left_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        # Create a tab for each camera
        self.camera_tabs: Dict[int, CameraTab] = {}
        for camera_index, camera_id in self.cameras:
            tab = CameraTab(self.notebook, self.api, camera_index, camera_id)
            self.notebook.add(tab, text=camera_id)
            self.camera_tabs[camera_index] = tab

        # For backward compatibility, keep references to first camera's panels
        if self.camera_tabs:
            first_tab = self.camera_tabs[self.cameras[0][0]]
            self.camera_panel = first_tab.camera_panel
            self.subarray_panel = first_tab.subarray_panel
            self.display_panel = first_tab.display_panel

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
            # Update all camera tabs
            for camera_index, tab in self.camera_tabs.items():
                tab.update_from_state(state)

            # Update status bar (shared)
            self.status_bar.update_from_state(state)

            # Update focus window if it's open
            if hasattr(self, '_focus_window') and self._focus_window and self._focus_window.winfo_exists():
                self._focus_window.update_from_state(state)

            # Update camera settings window if it's open
            if hasattr(self, '_camera_settings_window') and self._camera_settings_window and self._camera_settings_window.winfo_exists():
                self._camera_settings_window.update_from_state(state)

            # Update telescope settings window if it's open
            if hasattr(self, '_telescope_settings_window') and self._telescope_settings_window and self._telescope_settings_window.winfo_exists():
                self._telescope_settings_window.update_from_state(state)
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
        self._focus_window = FocusWindow(self.root, self.api, enable_simulation=self.enable_simulation)
        self._focus_window.update_from_state(self.api.state)

    def _open_camera_settings_window(self):
        """Open the camera settings window."""
        # If window already exists and is open, just raise it
        if self._camera_settings_window and self._camera_settings_window.winfo_exists():
            self._camera_settings_window.lift()
            self._camera_settings_window.focus()
            return

        # Create new camera settings window
        self._camera_settings_window = CameraSettingsWindow(self.root, self.api)
        self._camera_settings_window.update_from_state(self.api.state)

    def _open_telescope_settings_window(self):
        """Open the telescope settings window."""
        # If window already exists and is open, just raise it
        if self._telescope_settings_window and self._telescope_settings_window.winfo_exists():
            self._telescope_settings_window.lift()
            self._telescope_settings_window.focus()
            return

        # Create new telescope settings window
        self._telescope_settings_window = TelescopeSettingsWindow(self.root, self.api)
        self._telescope_settings_window.update_from_state(self.api.state)

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
            config = get_config()

            # Auto-connect telescope (if enabled in config)
            if config.telescope.auto_connect:
                try:
                    if self.api.connect_telescope():
                        logger.info("Telescope auto-connected")
                    else:
                        logger.info("Telescope not available for auto-connect")
                except Exception as e:
                    logger.debug(f"Telescope auto-connect failed: {e}")

            # Auto-connect all cameras
            for camera_index, camera_id in self.cameras:
                try:
                    if self.api.connect_camera(camera_index=camera_index):
                        logger.info(f"Camera auto-connected (index={camera_index}, ID={camera_id})")
                    else:
                        logger.info(f"Camera {camera_index} ({camera_id}) not available for auto-connect")
                except Exception as e:
                    logger.debug(f"Camera {camera_index} auto-connect failed: {e}")

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

            # Cleanup all camera tabs
            for camera_index, tab in self.camera_tabs.items():
                tab.cleanup()

            # Cleanup API
            try:
                # Stop streaming on all cameras first
                for camera_index in self.api.cameras.keys():
                    cam_state = self.api.state.get_camera(camera_index)
                    if cam_state.streaming:
                        self.api.stop_streaming(camera_index=camera_index)
                    # Brief pause for in-flight frames
                    if cam_state.is_saving:
                        import time
                        time.sleep(0.05)
                        self.api.stop_saving(camera_index=camera_index)
                    if cam_state.connected:
                        self.api.disconnect_camera(camera_index=camera_index)

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
    import argparse

    parser = argparse.ArgumentParser(description="Cerberus High-Speed Imager GUI")
    parser.add_argument('--sim', '--simulate', action='store_true',
                        help='Enable simulation mode for focus loop testing')
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create and run GUI
    gui = CerberusGUI(enable_simulation=args.sim)
    gui.run()


if __name__ == "__main__":
    main()
