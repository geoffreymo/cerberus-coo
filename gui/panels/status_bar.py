# gui/panels/status_bar.py
"""Status bar for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, List, Tuple, Dict

if TYPE_CHECKING:
    from ...api import CerberusAPI


class StatusBar(ttk.Frame):
    """
    Status bar showing connection status for all cameras, telescope, and filterwheel.
    """

    def __init__(self, parent, api: 'CerberusAPI', cameras: List[Tuple[int, str]] = None):
        super().__init__(parent)
        self.api = api
        self.cameras = cameras or [(0, "Camera 0")]

        # Per-camera status variables
        self.camera_status_vars: Dict[int, tk.StringVar] = {}
        self.camera_labels: Dict[int, ttk.Label] = {}

        # Shared status variables
        self.telescope_status_var = tk.StringVar(value="TCS: Disconnected")
        self.filterwheel_status_var = tk.StringVar(value="Filter: Disconnected")
        self.gps_status_var = tk.StringVar(value="GPS: ○")

        self._create_widgets()

    def _create_widgets(self):
        """Create status bar widgets showing all cameras."""
        # Configure grid columns (3 columns for telescope, filter, GPS)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)

        row = 0

        # Per-camera status rows
        for camera_index, camera_id in self.cameras:
            self.camera_status_vars[camera_index] = tk.StringVar(value=f"{camera_id}: Disconnected")

            label = ttk.Label(
                self, textvariable=self.camera_status_vars[camera_index],
                relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2)
            )
            label.grid(row=row, column=0, columnspan=3, sticky="ew", pady=1)
            self.camera_labels[camera_index] = label
            row += 1

        # Telescope status
        self.telescope_label = ttk.Label(
            self, textvariable=self.telescope_status_var,
            relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2)
        )
        self.telescope_label.grid(row=row, column=0, sticky="ew", padx=(0, 2), pady=1)

        # Filter status
        self.filter_label = ttk.Label(
            self, textvariable=self.filterwheel_status_var,
            relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2)
        )
        self.filter_label.grid(row=row, column=1, sticky="ew", padx=2, pady=1)

        # GPS status
        self.gps_label = ttk.Label(
            self, textvariable=self.gps_status_var,
            relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2)
        )
        self.gps_label.grid(row=row, column=2, sticky="ew", padx=(2, 0), pady=1)

    def update_from_state(self, state):
        """Update status bar from system state."""
        # Per-camera status
        for camera_index, camera_id in self.cameras:
            cam_state = state.get_camera(camera_index)

            if cam_state.connected:
                if cam_state.streaming:
                    status = "Streaming"
                    if cam_state.is_saving:
                        status = f"Saving ({cam_state.frames_saved})"
                    color = "green"
                else:
                    status = "Connected"
                    color = "blue"

                # Add temperature if available
                if cam_state.temperature is not None:
                    temp_str = f" {cam_state.temperature:.1f}C"
                else:
                    temp_str = ""

                self.camera_status_vars[camera_index].set(f"{camera_id}: {status}{temp_str}")
                self.camera_labels[camera_index].config(foreground=color)
            else:
                self.camera_status_vars[camera_index].set(f"{camera_id}: Disconnected")
                self.camera_labels[camera_index].config(foreground="gray")

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

        # GPS status
        if state.gps_connected:
            self.gps_status_var.set("GPS: ●")
            self.gps_label.config(foreground="green")
        else:
            self.gps_status_var.set("GPS: ○")
            self.gps_label.config(foreground="gray")
