# gui/panels/status_bar.py
"""Status bar for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...api import CerberusAPI


class StatusBar(ttk.Frame):
    """
    Status bar showing connection status, temperature, and frame rate.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent)
        self.api = api

        # Variables
        self.camera_status_var = tk.StringVar(value="Camera: Disconnected")
        self.telescope_status_var = tk.StringVar(value="TCS: Disconnected")
        self.filterwheel_status_var = tk.StringVar(value="Filter: Disconnected")
        self.temperature_var = tk.StringVar(value="Temp: --")
        self.fps_var = tk.StringVar(value="FPS: --")
        self.frames_var = tk.StringVar(value="Frames: 0")

        self._create_widgets()

    def _create_widgets(self):
        """Create status bar widgets."""
        # Camera status
        self.camera_label = ttk.Label(
            self, textvariable=self.camera_status_var,
            relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2)
        )
        self.camera_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Separator
        ttk.Separator(self, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=2)

        # Telescope status
        self.telescope_label = ttk.Label(
            self, textvariable=self.telescope_status_var,
            relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2)
        )
        self.telescope_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Separator
        ttk.Separator(self, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=2)

        # Filter status
        self.filter_label = ttk.Label(
            self, textvariable=self.filterwheel_status_var,
            relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2)
        )
        self.filter_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Separator
        ttk.Separator(self, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=2)

        # Temperature
        self.temp_label = ttk.Label(
            self, textvariable=self.temperature_var,
            relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2), width=15
        )
        self.temp_label.pack(side=tk.LEFT)

        # Separator
        ttk.Separator(self, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=2)

        # FPS
        self.fps_label = ttk.Label(
            self, textvariable=self.fps_var,
            relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2), width=12
        )
        self.fps_label.pack(side=tk.LEFT)

        # Separator
        ttk.Separator(self, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=2)

        # Frames
        self.frames_label = ttk.Label(
            self, textvariable=self.frames_var,
            relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2), width=15
        )
        self.frames_label.pack(side=tk.LEFT)

    def update_from_state(self, state):
        """Update status bar from system state."""
        # Camera status
        if state.camera_connected:
            if state.camera_streaming:
                self.camera_status_var.set("Camera: Streaming")
                self.camera_label.config(foreground="green")
            else:
                self.camera_status_var.set("Camera: Connected")
                self.camera_label.config(foreground="blue")
        else:
            self.camera_status_var.set("Camera: Disconnected")
            self.camera_label.config(foreground="gray")

        # Telescope status
        if state.telescope_connected:
            focus = f"{state.telescope_focus:.1f}mm" if state.telescope_focus else "--"
            self.telescope_status_var.set(f"TCS: {focus}")
            self.telescope_label.config(foreground="green")
        else:
            self.telescope_status_var.set("TCS: Disconnected")
            self.telescope_label.config(foreground="gray")

        # Filter status
        if state.filterwheel_connected:
            filter_name = state.current_filter or "--"
            self.filterwheel_status_var.set(f"Filter: {filter_name}")
            self.filter_label.config(foreground="green")
        else:
            self.filterwheel_status_var.set("Filter: Disconnected")
            self.filter_label.config(foreground="gray")

        # Temperature
        if state.camera_temperature is not None:
            self.temperature_var.set(f"Temp: {state.camera_temperature:.1f}C")
        else:
            self.temperature_var.set("Temp: --")

        # FPS
        if state.camera_frame_rate is not None:
            self.fps_var.set(f"FPS: {state.camera_frame_rate:.1f}")
        else:
            self.fps_var.set("FPS: --")

        # Frames
        self.frames_var.set(f"Frames: {state.camera_frames_captured}")
