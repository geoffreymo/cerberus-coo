# gui/panels/subarray_panel.py
"""Subarray controls panel for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...api import CerberusAPI


class SubarrayPanel(ttk.LabelFrame):
    """
    Panel for subarray (ROI) controls.

    Allows setting horizontal/vertical position and size for region-of-interest.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent, text="Subarray (ROI)", padding=5)
        self.api = api

        # Variables
        self.enabled_var = tk.BooleanVar(value=False)
        self.hpos_var = tk.StringVar(value="0")
        self.hsize_var = tk.StringVar(value="4096")
        self.vpos_var = tk.StringVar(value="0")
        self.vsize_var = tk.StringVar(value="2304")

        self._create_widgets()

    def _create_widgets(self):
        """Create panel widgets."""
        # Enable checkbox
        enable_frame = ttk.Frame(self)
        enable_frame.pack(fill=tk.X, pady=2)

        self.enable_checkbox = ttk.Checkbutton(
            enable_frame, text="Enable Subarray", variable=self.enabled_var,
            command=self._on_enable_toggle
        )
        self.enable_checkbox.pack(side=tk.LEFT)

        # Position/Size grid
        grid_frame = ttk.Frame(self)
        grid_frame.pack(fill=tk.X, pady=2)

        # Row 0: HPOS, HSIZE
        ttk.Label(grid_frame, text="HPOS:").grid(row=0, column=0, sticky=tk.W)
        self.hpos_entry = ttk.Entry(
            grid_frame, textvariable=self.hpos_var, width=6, state=tk.DISABLED
        )
        self.hpos_entry.grid(row=0, column=1, padx=2)

        ttk.Label(grid_frame, text="HSIZE:").grid(row=0, column=2, sticky=tk.W, padx=(10, 0))
        self.hsize_entry = ttk.Entry(
            grid_frame, textvariable=self.hsize_var, width=6, state=tk.DISABLED
        )
        self.hsize_entry.grid(row=0, column=3, padx=2)

        # Row 1: VPOS, VSIZE
        ttk.Label(grid_frame, text="VPOS:").grid(row=1, column=0, sticky=tk.W)
        self.vpos_entry = ttk.Entry(
            grid_frame, textvariable=self.vpos_var, width=6, state=tk.DISABLED
        )
        self.vpos_entry.grid(row=1, column=1, padx=2)

        ttk.Label(grid_frame, text="VSIZE:").grid(row=1, column=2, sticky=tk.W, padx=(10, 0))
        self.vsize_entry = ttk.Entry(
            grid_frame, textvariable=self.vsize_var, width=6, state=tk.DISABLED
        )
        self.vsize_entry.grid(row=1, column=3, padx=2)

        # Apply and Reset buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=2)

        self.apply_btn = ttk.Button(
            btn_frame, text="Apply", command=self._on_apply, state=tk.DISABLED
        )
        self.apply_btn.pack(side=tk.LEFT, padx=2)

        self.reset_btn = ttk.Button(
            btn_frame, text="Reset to Full Frame", command=self._on_reset
        )
        self.reset_btn.pack(side=tk.LEFT, padx=2)

        # Note
        note_label = ttk.Label(
            self, text="Values rounded to nearest 4",
            font=("TkDefaultFont", 9), foreground="gray"
        )
        note_label.pack(anchor=tk.W)

    def _on_enable_toggle(self):
        """Handle enable checkbox toggle."""
        enabled = self.enabled_var.get()
        state = tk.NORMAL if enabled else tk.DISABLED

        self.hpos_entry.config(state=state)
        self.hsize_entry.config(state=state)
        self.vpos_entry.config(state=state)
        self.vsize_entry.config(state=state)
        self.apply_btn.config(state=state)

        # Set subarray mode
        # SUBARRAY_MODE: 1.0 = OFF, 2.0 = ON
        mode = 2.0 if enabled else 1.0
        self.api.set_camera_property("SUBARRAY_MODE", mode)

    def _on_apply(self):
        """Apply subarray settings."""
        try:
            hpos = int(self.hpos_var.get())
            hsize = int(self.hsize_var.get())
            vpos = int(self.vpos_var.get())
            vsize = int(self.vsize_var.get())

            # Round to nearest 4
            hpos = (hpos // 4) * 4
            hsize = (hsize // 4) * 4
            vpos = (vpos // 4) * 4
            vsize = (vsize // 4) * 4

            # Update display with rounded values
            self.hpos_var.set(str(hpos))
            self.hsize_var.set(str(hsize))
            self.vpos_var.set(str(vpos))
            self.vsize_var.set(str(vsize))

            # Apply to camera
            self.api.set_camera_property("SUBARRAY_HPOS", float(hpos))
            self.api.set_camera_property("SUBARRAY_HSIZE", float(hsize))
            self.api.set_camera_property("SUBARRAY_VPOS", float(vpos))
            self.api.set_camera_property("SUBARRAY_VSIZE", float(vsize))

        except ValueError:
            pass

    def _on_reset(self):
        """Reset subarray to full frame."""
        # Disable subarray mode first
        self.enabled_var.set(False)
        self._on_enable_toggle()

        # Reset to full frame values
        self.hpos_var.set("0")
        self.hsize_var.set("4096")
        self.vpos_var.set("0")
        self.vsize_var.set("2304")

    def update_from_state(self, state):
        """Update panel from system state."""
        # Could be extended to read current values from camera
        pass
