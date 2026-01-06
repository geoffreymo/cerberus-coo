# gui/panels/camera_settings.py
"""Camera settings panel for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...api import CerberusAPI


class CameraSettingsPanel(ttk.LabelFrame):
    """
    Panel for camera settings.

    Includes binning, readout speed, sensor mode, trigger settings,
    defect correction, and hot pixel level.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent, text="Camera Settings", padding=5)
        self.api = api

        # Variables
        self.binning_var = tk.StringVar(value="1x1")
        self.readout_speed_var = tk.StringVar(value="Standard")
        self.sensor_mode_var = tk.StringVar(value="Standard")
        self.trigger_source_var = tk.StringVar(value="Internal")
        self.trigger_mode_var = tk.StringVar(value="Normal")
        self.defect_correct_var = tk.StringVar(value="OFF")
        self.hot_pixel_var = tk.StringVar(value="MINIMUM")

        self._create_widgets()

    def _create_widgets(self):
        """Create panel widgets."""
        row = 0

        # Binning
        ttk.Label(self, text="Binning:").grid(row=row, column=0, sticky=tk.W, pady=1)
        binning_menu = ttk.Combobox(
            self, textvariable=self.binning_var, width=12, state="readonly",
            values=["1x1", "2x2", "4x4"]
        )
        binning_menu.grid(row=row, column=1, pady=1)
        binning_menu.bind("<<ComboboxSelected>>", self._on_binning_change)
        row += 1

        # Readout Speed
        ttk.Label(self, text="Readout:").grid(row=row, column=0, sticky=tk.W, pady=1)
        readout_menu = ttk.Combobox(
            self, textvariable=self.readout_speed_var, width=12, state="readonly",
            values=["Ultra Quiet", "Standard"]
        )
        readout_menu.grid(row=row, column=1, pady=1)
        readout_menu.bind("<<ComboboxSelected>>", self._on_readout_change)
        row += 1

        # Sensor Mode
        ttk.Label(self, text="Sensor:").grid(row=row, column=0, sticky=tk.W, pady=1)
        sensor_menu = ttk.Combobox(
            self, textvariable=self.sensor_mode_var, width=12, state="readonly",
            values=["Standard", "Photon Number"]
        )
        sensor_menu.grid(row=row, column=1, pady=1)
        sensor_menu.bind("<<ComboboxSelected>>", self._on_sensor_mode_change)
        row += 1

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=3)
        row += 1

        # Trigger Source
        ttk.Label(self, text="Trig Source:").grid(row=row, column=0, sticky=tk.W, pady=1)
        trigger_source_menu = ttk.Combobox(
            self, textvariable=self.trigger_source_var, width=12, state="readonly",
            values=["Internal", "External", "Software"]
        )
        trigger_source_menu.grid(row=row, column=1, pady=1)
        trigger_source_menu.bind("<<ComboboxSelected>>", self._on_trigger_source_change)
        row += 1

        # Trigger Mode
        ttk.Label(self, text="Trig Mode:").grid(row=row, column=0, sticky=tk.W, pady=1)
        trigger_mode_menu = ttk.Combobox(
            self, textvariable=self.trigger_mode_var, width=12, state="readonly",
            values=["Normal", "Start"]
        )
        trigger_mode_menu.grid(row=row, column=1, pady=1)
        trigger_mode_menu.bind("<<ComboboxSelected>>", self._on_trigger_mode_change)
        row += 1

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=2, sticky="ew", pady=3)
        row += 1

        # Defect Correction
        ttk.Label(self, text="Defect Corr:").grid(row=row, column=0, sticky=tk.W, pady=1)
        defect_menu = ttk.Combobox(
            self, textvariable=self.defect_correct_var, width=12, state="readonly",
            values=["OFF", "ON"]
        )
        defect_menu.grid(row=row, column=1, pady=1)
        defect_menu.bind("<<ComboboxSelected>>", self._on_defect_correct_change)
        row += 1

        # Hot Pixel Level
        ttk.Label(self, text="Hot Pixel:").grid(row=row, column=0, sticky=tk.W, pady=1)
        hot_pixel_menu = ttk.Combobox(
            self, textvariable=self.hot_pixel_var, width=12, state="readonly",
            values=["STANDARD", "MINIMUM", "AGGRESSIVE"]
        )
        hot_pixel_menu.grid(row=row, column=1, pady=1)
        hot_pixel_menu.bind("<<ComboboxSelected>>", self._on_hot_pixel_change)

    def _on_binning_change(self, event=None):
        """Handle binning change."""
        if not self.api.state.camera_connected:
            return

        try:
            value = self.binning_var.get()
            binning_map = {"1x1": 1, "2x2": 2, "4x4": 4}
            if value in binning_map:
                self.api.set_binning(binning_map[value])
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to set binning: {e}")

    def _on_readout_change(self, event=None):
        """Handle readout speed change."""
        if not self.api.state.camera_connected:
            return

        try:
            value = self.readout_speed_var.get()
            # READOUT_SPEED: 1.0 = Ultra Quiet, 2.0 = Standard
            speed_map = {"Ultra Quiet": 1.0, "Standard": 2.0}
            if value in speed_map:
                self.api.set_camera_property("READOUT_SPEED", speed_map[value])
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to set readout speed: {e}")

    def _on_sensor_mode_change(self, event=None):
        """Handle sensor mode change."""
        if not self.api.state.camera_connected:
            return

        try:
            value = self.sensor_mode_var.get()
            # SENSOR_MODE: 1.0 = Standard, 12.0 = Photon Number Resolving
            mode_map = {"Standard": 1.0, "Photon Number": 12.0}
            if value in mode_map:
                self.api.set_camera_property("SENSOR_MODE", mode_map[value])
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to set sensor mode: {e}")

    def _on_trigger_source_change(self, event=None):
        """Handle trigger source change."""
        if not self.api.state.camera_connected:
            return

        try:
            value = self.trigger_source_var.get()
            # TRIGGER_SOURCE: 1.0 = Internal, 2.0 = External, 3.0 = Software
            source_map = {"Internal": 1.0, "External": 2.0, "Software": 3.0}
            if value in source_map:
                self.api.set_camera_property("TRIGGER_SOURCE", source_map[value])
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to set trigger source: {e}")

    def _on_trigger_mode_change(self, event=None):
        """Handle trigger mode change."""
        if not self.api.state.camera_connected:
            return

        try:
            value = self.trigger_mode_var.get()
            # TRIGGER_MODE: 1.0 = Normal, 6.0 = Start
            mode_map = {"Normal": 1.0, "Start": 6.0}
            if value in mode_map:
                self.api.set_camera_property("TRIGGER_MODE", mode_map[value])
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to set trigger mode: {e}")

    def _on_defect_correct_change(self, event=None):
        """Handle defect correction change."""
        if not self.api.state.camera_connected:
            return

        try:
            value = self.defect_correct_var.get()
            # DEFECT_CORRECT_MODE: 1.0 = OFF, 2.0 = ON
            mode_map = {"OFF": 1.0, "ON": 2.0}
            if value in mode_map:
                self.api.set_camera_property("DEFECT_CORRECT_MODE", mode_map[value])
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to set defect correction: {e}")

    def _on_hot_pixel_change(self, event=None):
        """Handle hot pixel level change."""
        if not self.api.state.camera_connected:
            return

        try:
            value = self.hot_pixel_var.get()
            # HOT_PIXEL_CORRECT_LEVEL: 1.0 = Standard, 2.0 = Minimum, 3.0 = Aggressive
            level_map = {"STANDARD": 1.0, "MINIMUM": 2.0, "AGGRESSIVE": 3.0}
            if value in level_map:
                self.api.set_camera_property("HOT_PIXEL_CORRECT_LEVEL", level_map[value])
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to set hot pixel level: {e}")

    def update_from_state(self, state):
        """Update panel from system state."""
        if not state.camera_connected or not state.camera_params:
            return

        try:
            self._do_update_from_state(state)
        except tk.TclError:
            pass  # Ignore 'popdown' errors when combobox dropdown is open

    def _do_update_from_state(self, state):
        """Internal update - may raise TclError if combobox is open."""
        params = state.camera_params

        # Update binning
        binning_val = params.get('BINNING')
        if binning_val:
            binning_map = {1: "1x1", 2: "2x2", 4: "4x4", "1x1": "1x1", "2x2": "2x2", "4x4": "4x4"}
            if binning_val in binning_map:
                self.binning_var.set(binning_map[binning_val])

        # Update readout speed
        readout_val = params.get('READOUT SPEED')
        if readout_val:
            if 'ULTRA QUIET' in str(readout_val).upper():
                self.readout_speed_var.set("Ultra Quiet")
            elif 'STANDARD' in str(readout_val).upper():
                self.readout_speed_var.set("Standard")

        # Update sensor mode
        sensor_val = params.get('SENSOR MODE')
        if sensor_val:
            if 'PHOTON' in str(sensor_val).upper():
                self.sensor_mode_var.set("Photon Number")
            else:
                self.sensor_mode_var.set("Standard")

        # Update trigger source
        trigger_source = params.get('TRIGGER SOURCE')
        if trigger_source:
            source_str = str(trigger_source).upper()
            if 'EXTERNAL' in source_str:
                self.trigger_source_var.set("External")
            elif 'SOFTWARE' in source_str:
                self.trigger_source_var.set("Software")
            else:
                self.trigger_source_var.set("Internal")

        # Update trigger mode
        trigger_mode = params.get('TRIGGER MODE')
        if trigger_mode:
            mode_str = str(trigger_mode).upper()
            if 'START' in mode_str:
                self.trigger_mode_var.set("Start")
            else:
                self.trigger_mode_var.set("Normal")

        # Update defect correction
        defect_val = params.get('DEFECT CORRECT MODE')
        if defect_val:
            if str(defect_val).upper() == 'ON' or defect_val == 2.0:
                self.defect_correct_var.set("ON")
            else:
                self.defect_correct_var.set("OFF")

        # Update hot pixel level
        hot_pixel_val = params.get('HOT PIXEL CORRECT LEVEL')
        if hot_pixel_val:
            hp_str = str(hot_pixel_val).upper()
            if 'AGGRESSIVE' in hp_str:
                self.hot_pixel_var.set("AGGRESSIVE")
            elif 'MINIMUM' in hp_str:
                self.hot_pixel_var.set("MINIMUM")
            else:
                self.hot_pixel_var.set("STANDARD")
