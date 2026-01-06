# gui/camera_settings_window.py
"""Camera settings window for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..api import CerberusAPI

logger = logging.getLogger(__name__)


class CameraSettingsWindow(tk.Toplevel):
    """
    Window showing all camera settings.

    Includes dropdowns for common settings and a full list of all parameters.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent)
        self.api = api
        self.title("Camera Settings")
        self.geometry("550x700")
        self.minsize(500, 600)

        # Variables for settings
        self.binning_var = tk.StringVar(value="1x1")
        self.sensor_mode_var = tk.StringVar(value="Standard")
        self.trigger_source_var = tk.StringVar(value="Internal")
        self.trigger_mode_var = tk.StringVar(value="Normal")
        self.defect_correct_var = tk.StringVar(value="OFF")
        self.hot_pixel_var = tk.StringVar(value="MINIMUM")

        self._create_widgets()

        # Initial update
        self.update_from_state(api.state)

    def _create_widgets(self):
        """Create window widgets."""
        # Main container with padding
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Settings frame at top
        settings_frame = ttk.LabelFrame(main_frame, text="Settings", padding=5)
        settings_frame.pack(fill=tk.X, pady=(0, 10))

        # Create settings grid
        row = 0

        # Binning
        ttk.Label(settings_frame, text="Binning:").grid(row=row, column=0, sticky=tk.W, pady=2, padx=5)
        binning_menu = ttk.Combobox(
            settings_frame, textvariable=self.binning_var, width=15, state="readonly",
            values=["1x1", "2x2", "4x4"]
        )
        binning_menu.grid(row=row, column=1, pady=2, padx=5, sticky=tk.W)
        binning_menu.bind("<<ComboboxSelected>>", self._on_binning_change)
        row += 1

        # Sensor Mode
        ttk.Label(settings_frame, text="Sensor Mode:").grid(row=row, column=0, sticky=tk.W, pady=2, padx=5)
        sensor_menu = ttk.Combobox(
            settings_frame, textvariable=self.sensor_mode_var, width=15, state="readonly",
            values=["Standard", "Photon Number"]
        )
        sensor_menu.grid(row=row, column=1, pady=2, padx=5, sticky=tk.W)
        sensor_menu.bind("<<ComboboxSelected>>", self._on_sensor_mode_change)
        row += 1

        # Trigger Source
        ttk.Label(settings_frame, text="Trigger Source:").grid(row=row, column=0, sticky=tk.W, pady=2, padx=5)
        trigger_source_menu = ttk.Combobox(
            settings_frame, textvariable=self.trigger_source_var, width=15, state="readonly",
            values=["Internal", "External", "Software"]
        )
        trigger_source_menu.grid(row=row, column=1, pady=2, padx=5, sticky=tk.W)
        trigger_source_menu.bind("<<ComboboxSelected>>", self._on_trigger_source_change)
        row += 1

        # Trigger Mode
        ttk.Label(settings_frame, text="Trigger Mode:").grid(row=row, column=0, sticky=tk.W, pady=2, padx=5)
        trigger_mode_menu = ttk.Combobox(
            settings_frame, textvariable=self.trigger_mode_var, width=15, state="readonly",
            values=["Normal", "Start"]
        )
        trigger_mode_menu.grid(row=row, column=1, pady=2, padx=5, sticky=tk.W)
        trigger_mode_menu.bind("<<ComboboxSelected>>", self._on_trigger_mode_change)
        row += 1

        # Defect Correction
        ttk.Label(settings_frame, text="Defect Correction:").grid(row=row, column=0, sticky=tk.W, pady=2, padx=5)
        defect_menu = ttk.Combobox(
            settings_frame, textvariable=self.defect_correct_var, width=15, state="readonly",
            values=["OFF", "ON"]
        )
        defect_menu.grid(row=row, column=1, pady=2, padx=5, sticky=tk.W)
        defect_menu.bind("<<ComboboxSelected>>", self._on_defect_correct_change)
        row += 1

        # Hot Pixel Level
        ttk.Label(settings_frame, text="Hot Pixel Level:").grid(row=row, column=0, sticky=tk.W, pady=2, padx=5)
        hot_pixel_menu = ttk.Combobox(
            settings_frame, textvariable=self.hot_pixel_var, width=15, state="readonly",
            values=["STANDARD", "MINIMUM", "AGGRESSIVE"]
        )
        hot_pixel_menu.grid(row=row, column=1, pady=2, padx=5, sticky=tk.W)
        hot_pixel_menu.bind("<<ComboboxSelected>>", self._on_hot_pixel_change)

        # All Parameters frame
        params_frame = ttk.LabelFrame(main_frame, text="All Camera Parameters", padding=5)
        params_frame.pack(fill=tk.BOTH, expand=True)

        # Configure smaller font for treeview
        style = ttk.Style()
        style.configure("Small.Treeview", font=('TkDefaultFont', 9), rowheight=18)
        style.configure("Small.Treeview.Heading", font=('TkDefaultFont', 9, 'bold'))

        # Treeview for parameters
        columns = ('value',)
        self.params_tree = ttk.Treeview(params_frame, columns=columns, show='tree headings', height=15, style="Small.Treeview")
        self.params_tree.heading('#0', text='Parameter')
        self.params_tree.heading('value', text='Value')
        self.params_tree.column('#0', width=200)
        self.params_tree.column('value', width=250)

        # Scrollbar
        scrollbar = ttk.Scrollbar(params_frame, orient=tk.VERTICAL, command=self.params_tree.yview)
        self.params_tree.configure(yscrollcommand=scrollbar.set)

        self.params_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Close button
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side=tk.RIGHT)

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
            logger.error(f"Failed to set binning: {e}")

    def _on_sensor_mode_change(self, event=None):
        """Handle sensor mode change."""
        if not self.api.state.camera_connected:
            return
        try:
            value = self.sensor_mode_var.get()
            mode_map = {"Standard": 1.0, "Photon Number": 12.0}
            if value in mode_map:
                self.api.set_camera_property("SENSOR_MODE", mode_map[value])
        except Exception as e:
            logger.error(f"Failed to set sensor mode: {e}")

    def _on_trigger_source_change(self, event=None):
        """Handle trigger source change."""
        if not self.api.state.camera_connected:
            return
        try:
            value = self.trigger_source_var.get()
            source_map = {"Internal": 1.0, "External": 2.0, "Software": 3.0}
            if value in source_map:
                self.api.set_camera_property("TRIGGER_SOURCE", source_map[value])
        except Exception as e:
            logger.error(f"Failed to set trigger source: {e}")

    def _on_trigger_mode_change(self, event=None):
        """Handle trigger mode change."""
        if not self.api.state.camera_connected:
            return
        try:
            value = self.trigger_mode_var.get()
            mode_map = {"Normal": 1.0, "Start": 6.0}
            if value in mode_map:
                self.api.set_camera_property("TRIGGER_MODE", mode_map[value])
        except Exception as e:
            logger.error(f"Failed to set trigger mode: {e}")

    def _on_defect_correct_change(self, event=None):
        """Handle defect correction change."""
        if not self.api.state.camera_connected:
            return
        try:
            value = self.defect_correct_var.get()
            mode_map = {"OFF": 1.0, "ON": 2.0}
            if value in mode_map:
                self.api.set_camera_property("DEFECT_CORRECT_MODE", mode_map[value])
        except Exception as e:
            logger.error(f"Failed to set defect correction: {e}")

    def _on_hot_pixel_change(self, event=None):
        """Handle hot pixel level change."""
        if not self.api.state.camera_connected:
            return
        try:
            value = self.hot_pixel_var.get()
            level_map = {"STANDARD": 1.0, "MINIMUM": 2.0, "AGGRESSIVE": 3.0}
            if value in level_map:
                self.api.set_camera_property("HOT_PIXEL_CORRECT_LEVEL", level_map[value])
        except Exception as e:
            logger.error(f"Failed to set hot pixel level: {e}")

    def update_from_state(self, state):
        """Update window from system state."""
        if not state.camera_connected or not state.camera_params:
            return

        try:
            params = state.camera_params

            # Update dropdowns
            self._update_dropdowns(params)

            # Update full params list
            self._update_params_tree(params)

        except tk.TclError:
            pass  # Ignore popdown errors

    def _update_dropdowns(self, params):
        """Update dropdown values from params."""
        # Binning
        binning_val = params.get('BINNING')
        if binning_val:
            binning_map = {1: "1x1", 2: "2x2", 4: "4x4", "1x1": "1x1", "2x2": "2x2", "4x4": "4x4"}
            if binning_val in binning_map:
                self.binning_var.set(binning_map[binning_val])

        # Sensor mode
        sensor_val = params.get('SENSOR MODE')
        if sensor_val:
            if 'PHOTON' in str(sensor_val).upper():
                self.sensor_mode_var.set("Photon Number")
            else:
                self.sensor_mode_var.set("Standard")

        # Trigger source
        trigger_source = params.get('TRIGGER SOURCE')
        if trigger_source:
            source_str = str(trigger_source).upper()
            if 'EXTERNAL' in source_str:
                self.trigger_source_var.set("External")
            elif 'SOFTWARE' in source_str:
                self.trigger_source_var.set("Software")
            else:
                self.trigger_source_var.set("Internal")

        # Trigger mode
        trigger_mode = params.get('TRIGGER MODE')
        if trigger_mode:
            mode_str = str(trigger_mode).upper()
            if 'START' in mode_str:
                self.trigger_mode_var.set("Start")
            else:
                self.trigger_mode_var.set("Normal")

        # Defect correction
        defect_val = params.get('DEFECT CORRECT MODE')
        if defect_val:
            if str(defect_val).upper() == 'ON' or defect_val == 2.0:
                self.defect_correct_var.set("ON")
            else:
                self.defect_correct_var.set("OFF")

        # Hot pixel level
        hot_pixel_val = params.get('HOT PIXEL CORRECT LEVEL')
        if hot_pixel_val:
            hp_str = str(hot_pixel_val).upper()
            if 'AGGRESSIVE' in hp_str:
                self.hot_pixel_var.set("AGGRESSIVE")
            elif 'MINIMUM' in hp_str:
                self.hot_pixel_var.set("MINIMUM")
            else:
                self.hot_pixel_var.set("STANDARD")

    def _update_params_tree(self, params):
        """Update the parameters treeview."""
        # Clear existing items
        for item in self.params_tree.get_children():
            self.params_tree.delete(item)

        # Add all parameters sorted alphabetically
        for key in sorted(params.keys()):
            value = params[key]
            self.params_tree.insert('', 'end', text=key, values=(value,))
