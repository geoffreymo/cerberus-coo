# gui/panels/camera_controls.py
"""Camera controls panel for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...api import CerberusAPI


class CameraControlsPanel(ttk.LabelFrame):
    """
    Panel for camera control settings.

    Includes exposure time, binning, trigger source, and capture controls.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent, text="Camera Controls", padding=5)
        self.api = api

        # Variables
        self.exposure_var = tk.StringVar(value="100")
        self.binning_var = tk.StringVar(value="1")
        self.trigger_var = tk.StringVar(value="External")

        self._create_widgets()

    def _create_widgets(self):
        """Create panel widgets."""
        # Exposure time
        exp_frame = ttk.Frame(self)
        exp_frame.pack(fill=tk.X, pady=2)

        ttk.Label(exp_frame, text="Exposure (ms):").pack(side=tk.LEFT)
        self.exposure_entry = ttk.Entry(
            exp_frame, textvariable=self.exposure_var, width=8
        )
        self.exposure_entry.pack(side=tk.LEFT, padx=5)
        self.exposure_entry.bind('<Return>', self._on_exposure_change)

        ttk.Button(
            exp_frame, text="Set", command=self._on_exposure_change, width=5
        ).pack(side=tk.LEFT)

        # Binning
        bin_frame = ttk.Frame(self)
        bin_frame.pack(fill=tk.X, pady=2)

        ttk.Label(bin_frame, text="Binning:").pack(side=tk.LEFT)
        self.binning_combo = ttk.Combobox(
            bin_frame,
            textvariable=self.binning_var,
            values=["1", "2", "4"],
            width=5,
            state="readonly"
        )
        self.binning_combo.pack(side=tk.LEFT, padx=5)
        self.binning_combo.bind('<<ComboboxSelected>>', self._on_binning_change)

        # Trigger source
        trig_frame = ttk.Frame(self)
        trig_frame.pack(fill=tk.X, pady=2)

        ttk.Label(trig_frame, text="Trigger:").pack(side=tk.LEFT)
        self.trigger_combo = ttk.Combobox(
            trig_frame,
            textvariable=self.trigger_var,
            values=["Internal", "External", "Software"],
            width=10,
            state="readonly"
        )
        self.trigger_combo.pack(side=tk.LEFT, padx=5)
        self.trigger_combo.bind('<<ComboboxSelected>>', self._on_trigger_change)

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # Capture controls
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=2)

        self.connect_btn = ttk.Button(
            btn_frame, text="Connect", command=self._on_connect
        )
        self.connect_btn.pack(side=tk.LEFT, padx=2)

        self.start_btn = ttk.Button(
            btn_frame, text="Start", command=self._on_start, state=tk.DISABLED
        )
        self.start_btn.pack(side=tk.LEFT, padx=2)

        self.stop_btn = ttk.Button(
            btn_frame, text="Stop", command=self._on_stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=2)

    def _on_connect(self, event=None):
        """Handle connect button click."""
        if self.api.state.camera_connected:
            self.api.disconnect_camera()
            self.connect_btn.config(text="Connect")
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.DISABLED)
        else:
            if self.api.connect_camera():
                self.connect_btn.config(text="Disconnect")
                self.start_btn.config(state=tk.NORMAL)
                # Update exposure from camera
                exp = self.api.get_exposure()
                if exp:
                    self.exposure_var.set(str(int(exp * 1000)))

    def _on_start(self, event=None):
        """Handle start button click."""
        if self.api.start_streaming():
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)

    def _on_stop(self, event=None):
        """Handle stop button click."""
        self.api.stop_streaming()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

    def _on_exposure_change(self, event=None):
        """Handle exposure change."""
        try:
            exp_ms = float(self.exposure_var.get())
            exp_sec = exp_ms / 1000.0
            self.api.set_exposure(exp_sec)
        except ValueError:
            pass

    def _on_binning_change(self, event=None):
        """Handle binning change."""
        try:
            binning = int(self.binning_var.get())
            self.api.set_binning(binning)
        except ValueError:
            pass

    def _on_trigger_change(self, event=None):
        """Handle trigger source change."""
        trigger = self.trigger_var.get().lower()
        self.api.set_trigger_source(trigger)

    def update_from_state(self, state):
        """Update panel from system state."""
        if state.camera_connected:
            self.connect_btn.config(text="Disconnect")
            if state.camera_streaming:
                self.start_btn.config(state=tk.DISABLED)
                self.stop_btn.config(state=tk.NORMAL)
            else:
                self.start_btn.config(state=tk.NORMAL)
                self.stop_btn.config(state=tk.DISABLED)
        else:
            self.connect_btn.config(text="Connect")
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.DISABLED)

        if state.camera_exposure:
            self.exposure_var.set(str(int(state.camera_exposure * 1000)))
