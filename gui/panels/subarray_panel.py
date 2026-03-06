# gui/panels/subarray_panel.py
"""Subarray controls panel for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Optional, Callable
import logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ...api import CerberusAPI


class SubarrayPanel(ttk.LabelFrame):
    """
    Panel for subarray (ROI) controls.

    Allows setting horizontal/vertical position and size for region-of-interest.
    Note: Subarray changes require stopping streaming first.
    """

    def __init__(self, parent, api: 'CerberusAPI', camera_index: int = 0):
        super().__init__(parent, text="Subarray (ROI)", padding=5)
        self.api = api
        self.camera_index = camera_index

        # Variables
        self.enabled_var = tk.BooleanVar(value=False)
        self.hpos_var = tk.StringVar(value="0")
        self.hsize_var = tk.StringVar(value="4096")
        self.vpos_var = tk.StringVar(value="0")
        self.vsize_var = tk.StringVar(value="2304")

        # Callback for when subarray is reset (so display can update offset)
        self.on_reset: Optional[Callable[[], None]] = None

        # Status label for messages
        self._status_var = tk.StringVar(value="")

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

        # Notes
        note_frame = ttk.Frame(self)
        note_frame.pack(fill=tk.X)

        ttk.Label(
            note_frame, text="Values rounded to nearest 4",
            font=("TkDefaultFont", 9), foreground="gray"
        ).pack(anchor=tk.W)

        ttk.Label(
            note_frame, text="SHIFT+drag on display to select ROI",
            font=("TkDefaultFont", 9), foreground="gray"
        ).pack(anchor=tk.W)

    def _on_enable_toggle(self):
        """Handle enable checkbox toggle."""
        enabled = self.enabled_var.get()
        state = tk.NORMAL if enabled else tk.DISABLED

        self.hpos_entry.config(state=state)
        self.hsize_entry.config(state=state)
        self.vpos_entry.config(state=state)
        self.vsize_entry.config(state=state)
        self.apply_btn.config(state=state)

        # Check if streaming - need to stop/restart
        cam_state = self.api.state.get_camera(self.camera_index)
        was_streaming = cam_state.streaming

        if was_streaming:
            logger.info(f"Stopping streaming on camera {self.camera_index} to change subarray mode...")
            self.api.stop_streaming(camera_index=self.camera_index)
            # Give camera time to stop
            self.after(200, lambda: self._apply_mode_change(enabled, was_streaming))
        else:
            self._apply_mode_change(enabled, False)

    def _apply_mode_change(self, enabled: bool, restart_streaming: bool):
        """Apply subarray mode change after streaming stopped."""
        # Set subarray mode
        # SUBARRAY_MODE: 1.0 = OFF, 2.0 = ON
        mode = 2.0 if enabled else 1.0
        result = self.api.set_camera_property("SUBARRAY_MODE", mode, camera_index=self.camera_index)

        if result:
            logger.info(f"Camera {self.camera_index} subarray mode set to {'ON' if enabled else 'OFF'}")
        else:
            logger.error(f"Failed to set subarray mode on camera {self.camera_index}")

        # Restart streaming if it was running
        if restart_streaming:
            self.after(100, lambda: self.api.start_streaming(camera_index=self.camera_index))

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

            # Check if streaming - need to stop/restart
            cam_state = self.api.state.get_camera(self.camera_index)
            was_streaming = cam_state.streaming

            if was_streaming:
                logger.info(f"Stopping streaming on camera {self.camera_index} to change subarray settings...")
                self.api.stop_streaming(camera_index=self.camera_index)
                # Give camera time to stop, then apply
                self.after(200, lambda: self._apply_subarray_params(
                    hpos, hsize, vpos, vsize, was_streaming))
            else:
                self._apply_subarray_params(hpos, hsize, vpos, vsize, False)

        except ValueError:
            logger.error("Invalid subarray values")

    def _apply_subarray_params(self, hpos: int, hsize: int, vpos: int, vsize: int,
                                restart_streaming: bool):
        """Apply subarray parameters after streaming stopped."""
        logger.info(f"Camera {self.camera_index}: Applying subarray: HPOS={hpos}, VPOS={vpos}, HSIZE={hsize}, VSIZE={vsize}")

        # Apply to camera
        self.api.set_camera_property("SUBARRAY_HPOS", float(hpos), camera_index=self.camera_index)
        self.api.set_camera_property("SUBARRAY_HSIZE", float(hsize), camera_index=self.camera_index)
        self.api.set_camera_property("SUBARRAY_VPOS", float(vpos), camera_index=self.camera_index)
        self.api.set_camera_property("SUBARRAY_VSIZE", float(vsize), camera_index=self.camera_index)

        # Restart streaming if it was running
        if restart_streaming:
            self.after(100, lambda: self.api.start_streaming(camera_index=self.camera_index))

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

        # Notify callback (e.g., to reset display panel offset)
        if self.on_reset:
            self.on_reset()

    def update_from_state(self, state, cam_state=None):
        """Update panel from system state.

        Args:
            state: SystemState object
            cam_state: CameraState object for this camera (optional, fetched if not provided)
        """
        # Could be extended to read current values from camera
        pass

    def apply_roi(self, hpos: int, vpos: int, hsize: int, vsize: int):
        """
        Apply ROI windowing settings from external source (e.g., image display drag).

        Args:
            hpos: Horizontal position (already rounded to 4)
            vpos: Vertical position (already rounded to 4)
            hsize: Horizontal size (already rounded to 4)
            vsize: Vertical size (already rounded to 4)
        """
        logger.info(f"Camera {self.camera_index}: Applying ROI from drag: {hsize}x{vsize} at ({hpos}, {vpos})")

        # Update UI state
        self.enabled_var.set(True)
        self.hpos_entry.config(state=tk.NORMAL)
        self.hsize_entry.config(state=tk.NORMAL)
        self.vpos_entry.config(state=tk.NORMAL)
        self.vsize_entry.config(state=tk.NORMAL)
        self.apply_btn.config(state=tk.NORMAL)

        # Set values in UI
        self.hpos_var.set(str(hpos))
        self.vpos_var.set(str(vpos))
        self.hsize_var.set(str(hsize))
        self.vsize_var.set(str(vsize))

        # Check if streaming - need to stop/restart
        cam_state = self.api.state.get_camera(self.camera_index)
        was_streaming = cam_state.streaming

        if was_streaming:
            logger.info(f"Stopping streaming on camera {self.camera_index} to apply ROI...")
            self.api.stop_streaming(camera_index=self.camera_index)
            # Give camera time to stop, then apply all settings
            self.after(200, lambda: self._apply_roi_settings(
                hpos, vpos, hsize, vsize, was_streaming))
        else:
            self._apply_roi_settings(hpos, vpos, hsize, vsize, False)

    def _apply_roi_settings(self, hpos: int, vpos: int, hsize: int, vsize: int,
                            restart_streaming: bool):
        """Apply all ROI settings at once after streaming stopped."""
        # Enable subarray mode first
        self.api.set_camera_property("SUBARRAY_MODE", 2.0, camera_index=self.camera_index)

        # Apply position/size
        self.api.set_camera_property("SUBARRAY_HPOS", float(hpos), camera_index=self.camera_index)
        self.api.set_camera_property("SUBARRAY_HSIZE", float(hsize), camera_index=self.camera_index)
        self.api.set_camera_property("SUBARRAY_VPOS", float(vpos), camera_index=self.camera_index)
        self.api.set_camera_property("SUBARRAY_VSIZE", float(vsize), camera_index=self.camera_index)

        logger.info(f"Camera {self.camera_index}: ROI applied: {hsize}x{vsize} at ({hpos}, {vpos})")

        # Restart streaming if it was running
        if restart_streaming:
            self.after(100, lambda: self.api.start_streaming(camera_index=self.camera_index))

    def get_current_offset(self) -> tuple:
        """Get current subarray offset (HPOS, VPOS)."""
        if self.enabled_var.get():
            try:
                return int(self.hpos_var.get()), int(self.vpos_var.get())
            except ValueError:
                pass
        return 0, 0
