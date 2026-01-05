# gui/panels/camera_controls.py
"""Camera controls panel for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk, filedialog
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...api import CerberusAPI


class CameraControlsPanel(ttk.LabelFrame):
    """
    Panel for camera control settings.

    Includes connection, exposure, streaming, and save controls.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent, text="Camera Controls", padding=5)
        self.api = api

        # Variables - Basic
        self.exposure_var = tk.StringVar(value="100")
        self.exposure_unit_var = tk.StringVar(value="ms")
        self._last_unit = "ms"  # Track unit for conversions

        # Variables - Take N Images
        self.n_images_var = tk.StringVar(value="1")
        self.image_progress_var = tk.StringVar(value="")
        self._taking_images = False
        self._target_frames = 0
        self._start_frame_count = 0

        # Variables - Streaming timer
        self.stream_time_var = tk.StringVar(value="")
        self._stream_start_time = 0
        self._timer_after_id = None

        # Variables - Save
        self.save_var = tk.BooleanVar(value=False)
        self.object_name_var = tk.StringVar(value="Object")
        self.output_dir_var = tk.StringVar(value="/data/cerberus")
        self.frames_per_cube_var = tk.StringVar(value="1000")

        # Stats
        self.frames_saved_var = tk.StringVar(value="0")
        self.cubes_saved_var = tk.StringVar(value="0")
        self.frames_dropped_var = tk.StringVar(value="0")

        self._create_widgets()

    def _create_widgets(self):
        """Create panel widgets."""
        # Connection row
        conn_frame = ttk.Frame(self)
        conn_frame.pack(fill=tk.X, pady=2)

        self.connect_btn = ttk.Button(
            conn_frame, text="Connect", command=self._on_connect
        )
        self.connect_btn.pack(side=tk.LEFT, padx=2)

        self.status_label = ttk.Label(conn_frame, text="Disconnected")
        self.status_label.pack(side=tk.LEFT, padx=10)

        # Exposure time
        exp_frame = ttk.Frame(self)
        exp_frame.pack(fill=tk.X, pady=2)

        ttk.Label(exp_frame, text="Exposure:").pack(side=tk.LEFT)
        self.exposure_entry = ttk.Entry(
            exp_frame, textvariable=self.exposure_var, width=8
        )
        self.exposure_entry.pack(side=tk.LEFT, padx=5)
        self.exposure_entry.bind('<Return>', self._on_exposure_change)

        # Unit selector
        self.exposure_unit_combo = ttk.Combobox(
            exp_frame, textvariable=self.exposure_unit_var,
            width=5, state="readonly", values=["ms", "s", "min"]
        )
        self.exposure_unit_combo.pack(side=tk.LEFT, padx=2)
        self.exposure_unit_combo.bind("<<ComboboxSelected>>", self._on_unit_change)

        ttk.Button(
            exp_frame, text="Set", command=self._on_exposure_change, width=5
        ).pack(side=tk.LEFT)

        # Start/Stop buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=2)

        self.start_btn = ttk.Button(
            btn_frame, text="Start Streaming", command=self._on_start, state=tk.DISABLED
        )
        self.start_btn.pack(side=tk.LEFT, padx=2)

        self.stop_btn = ttk.Button(
            btn_frame, text="Stop Streaming", command=self._on_stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=2)

        # Streaming timer
        self.stream_timer_label = ttk.Label(btn_frame, textvariable=self.stream_time_var)
        self.stream_timer_label.pack(side=tk.LEFT, padx=10)

        # Take N Images section
        take_frame = ttk.Frame(self)
        take_frame.pack(fill=tk.X, pady=2)

        ttk.Label(take_frame, text="Take").pack(side=tk.LEFT)
        ttk.Entry(
            take_frame, textvariable=self.n_images_var, width=5
        ).pack(side=tk.LEFT, padx=5)
        ttk.Label(take_frame, text="images:").pack(side=tk.LEFT)

        self.take_btn = ttk.Button(
            take_frame, text="Take Images", command=self._on_take_images, state=tk.DISABLED
        )
        self.take_btn.pack(side=tk.LEFT, padx=5)

        # Image progress
        self.progress_label = ttk.Label(take_frame, textvariable=self.image_progress_var)
        self.progress_label.pack(side=tk.LEFT, padx=5)

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # Save checkbox
        self.save_checkbox = ttk.Checkbutton(
            self, text="Save Data to Disk", variable=self.save_var,
            command=self._on_save_toggle
        )
        self.save_checkbox.pack(anchor=tk.W, pady=2)

        # Object name
        obj_frame = ttk.Frame(self)
        obj_frame.pack(fill=tk.X, pady=2)

        ttk.Label(obj_frame, text="Object:").pack(side=tk.LEFT)
        ttk.Entry(
            obj_frame, textvariable=self.object_name_var, width=15
        ).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        # Output directory
        dir_frame = ttk.Frame(self)
        dir_frame.pack(fill=tk.X, pady=2)

        ttk.Label(dir_frame, text="Directory:").pack(side=tk.LEFT)
        ttk.Entry(
            dir_frame, textvariable=self.output_dir_var, width=20
        ).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(
            dir_frame, text="...", command=self._browse_directory, width=3
        ).pack(side=tk.LEFT)

        # Frames per cube
        cube_frame = ttk.Frame(self)
        cube_frame.pack(fill=tk.X, pady=2)

        ttk.Label(cube_frame, text="Frames/Cube:").pack(side=tk.LEFT)
        ttk.Entry(
            cube_frame, textvariable=self.frames_per_cube_var, width=8
        ).pack(side=tk.LEFT, padx=5)

        # Stats row
        stats_frame = ttk.Frame(self)
        stats_frame.pack(fill=tk.X, pady=2)

        ttk.Label(stats_frame, text="Saved:").pack(side=tk.LEFT)
        ttk.Label(stats_frame, textvariable=self.frames_saved_var, width=8).pack(side=tk.LEFT)

        ttk.Label(stats_frame, text="Cubes:").pack(side=tk.LEFT)
        ttk.Label(stats_frame, textvariable=self.cubes_saved_var, width=4).pack(side=tk.LEFT)

        ttk.Label(stats_frame, text="Drop:").pack(side=tk.LEFT)
        self.dropped_label = ttk.Label(stats_frame, textvariable=self.frames_dropped_var, width=6)
        self.dropped_label.pack(side=tk.LEFT)

    def _browse_directory(self):
        """Open directory browser."""
        directory = filedialog.askdirectory(
            initialdir=self.output_dir_var.get(),
            title="Select Output Directory"
        )
        if directory:
            self.output_dir_var.set(directory)

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
                    # Convert to current unit
                    unit = self.exposure_unit_var.get()
                    if unit == "ms":
                        exp_display = exp * 1000.0
                    elif unit == "s":
                        exp_display = exp
                    elif unit == "min":
                        exp_display = exp / 60.0
                    else:
                        exp_display = exp * 1000.0
                    self.exposure_var.set(str(exp_display))

    def _on_start(self, event=None):
        """Handle start button click."""
        if self.api.start_streaming():
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)

            # Start streaming timer
            self._start_stream_timer()

            # Auto-start saving if checkbox is checked
            if self.save_var.get():
                self._start_saving()

    def _on_stop(self, event=None):
        """Handle stop button click."""
        # Stop saving first if active
        if self.api.state.is_saving:
            self.api.stop_saving()

        self.api.stop_streaming()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

        # Stop streaming timer
        self._stop_stream_timer()

        # Reset take images state if was running
        if self._taking_images:
            self._taking_images = False
            self.image_progress_var.set("")

    def _on_exposure_change(self, event=None):
        """Handle exposure change."""
        try:
            exp_value = float(self.exposure_var.get())
            unit = self.exposure_unit_var.get()

            # Convert to seconds based on unit
            if unit == "ms":
                exp_sec = exp_value / 1000.0
            elif unit == "s":
                exp_sec = exp_value
            elif unit == "min":
                exp_sec = exp_value * 60.0
            else:
                exp_sec = exp_value / 1000.0  # Default to ms

            self.api.set_exposure(exp_sec)
        except ValueError:
            pass

    def _on_unit_change(self, event=None):
        """Handle exposure unit change - convert displayed value."""
        try:
            # Get current value in current unit
            current_value = float(self.exposure_var.get())
            old_unit = self._last_unit if hasattr(self, '_last_unit') else "ms"
            new_unit = self.exposure_unit_var.get()

            # Convert to seconds first
            if old_unit == "ms":
                exp_sec = current_value / 1000.0
            elif old_unit == "s":
                exp_sec = current_value
            elif old_unit == "min":
                exp_sec = current_value * 60.0
            else:
                exp_sec = current_value / 1000.0

            # Convert to new unit
            if new_unit == "ms":
                new_value = exp_sec * 1000.0
            elif new_unit == "s":
                new_value = exp_sec
            elif new_unit == "min":
                new_value = exp_sec / 60.0
            else:
                new_value = exp_sec * 1000.0

            # Update display with full precision
            self.exposure_var.set(str(new_value))
            self._last_unit = new_unit

        except ValueError:
            pass

        # Store current unit for next conversion
        self._last_unit = self.exposure_unit_var.get()

    def _on_save_toggle(self):
        """Handle save checkbox toggle."""
        if self.save_var.get():
            # If streaming, start saving immediately
            if self.api.state.camera_streaming:
                self._start_saving()
        else:
            # Stop saving
            if self.api.state.is_saving:
                self.api.stop_saving()

    def _start_saving(self):
        """Start saving to disk."""
        import logging
        import os
        from tkinter import messagebox
        logger = logging.getLogger(__name__)

        try:
            frames_per_cube = int(self.frames_per_cube_var.get())
        except ValueError:
            frames_per_cube = 1000

        object_name = self.object_name_var.get()
        output_dir = self.output_dir_var.get()

        logger.info(f"_start_saving called: object={object_name}, dir={output_dir}, frames={frames_per_cube}")
        logger.info(f"Camera streaming: {self.api.state.camera_streaming}")

        # Check if output directory exists, create if not
        if not os.path.exists(output_dir):
            logger.info(f"Output directory does not exist, creating: {output_dir}")
            try:
                os.makedirs(output_dir, exist_ok=True)
                logger.info(f"Created output directory: {output_dir}")
            except Exception as e:
                logger.error(f"Failed to create output directory: {e}")
                messagebox.showerror("Directory Error", f"Cannot create output directory:\n{output_dir}\n\nError: {e}")
                self.save_var.set(False)
                return

        success = self.api.start_saving(
            object_name=object_name,
            output_dir=output_dir,
            frames_per_cube=frames_per_cube
        )

        logger.info(f"start_saving returned: {success}")

        if not success:
            logger.warning("start_saving failed, unchecking save checkbox")
            self.save_var.set(False)

    def _start_stream_timer(self):
        """Start the streaming timer."""
        import time
        self._stream_start_time = time.time()
        self._update_stream_timer()

    def _stop_stream_timer(self):
        """Stop the streaming timer."""
        if self._timer_after_id:
            self.after_cancel(self._timer_after_id)
            self._timer_after_id = None
        self.stream_time_var.set("")

    def _update_stream_timer(self):
        """Update streaming timer display."""
        import time
        if self._stream_start_time > 0:
            elapsed = int(time.time() - self._stream_start_time)
            hours = elapsed // 3600
            minutes = (elapsed % 3600) // 60
            seconds = elapsed % 60

            if hours > 0:
                time_str = f"Streaming: {hours:02d}h {minutes:02d}m {seconds:02d}s"
            elif minutes > 0:
                time_str = f"Streaming: {minutes:02d}m {seconds:02d}s"
            else:
                time_str = f"Streaming: {seconds:02d}s"

            self.stream_time_var.set(time_str)

            # Schedule next update
            self._timer_after_id = self.after(1000, self._update_stream_timer)

    def _on_take_images(self):
        """Handle take N images button click."""
        try:
            n_images = int(self.n_images_var.get())
            if n_images < 1:
                return
        except ValueError:
            return

        # Start streaming
        if not self.api.state.camera_streaming:
            if self.api.start_streaming():
                self.start_btn.config(state=tk.DISABLED)
                self.stop_btn.config(state=tk.NORMAL)
                self._start_stream_timer()
            else:
                return

        # Set up take images mode
        self._taking_images = True
        self._target_frames = n_images
        self._start_frame_count = self.api.state.camera_frames_captured
        self.image_progress_var.set(f"Taking: 0 / {n_images}")

    def _check_image_progress(self, current_frames):
        """Check if we've captured enough images."""
        if self._taking_images:
            frames_captured = current_frames - self._start_frame_count
            self.image_progress_var.set(f"Taking: {frames_captured} / {self._target_frames}")

            # Check if done
            if frames_captured >= self._target_frames:
                self._taking_images = False
                self.image_progress_var.set(f"Done: {frames_captured} images")

                # Auto-stop streaming
                if self.api.state.camera_streaming:
                    self.api.stop_streaming()
                    self.start_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
                    self._stop_stream_timer()

    def update_from_state(self, state):
        """Update panel from system state."""
        # Connection state
        if state.camera_connected:
            self.connect_btn.config(text="Disconnect")
            self.status_label.config(text="Connected", foreground="green")
            if state.camera_streaming:
                self.start_btn.config(state=tk.DISABLED)
                self.stop_btn.config(state=tk.NORMAL)
                self.take_btn.config(state=tk.DISABLED)  # Can't take while already streaming
            else:
                self.start_btn.config(state=tk.NORMAL)
                self.stop_btn.config(state=tk.DISABLED)
                self.take_btn.config(state=tk.NORMAL)  # Enable take images
        else:
            self.connect_btn.config(text="Connect")
            self.status_label.config(text="Disconnected", foreground="black")
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.DISABLED)
            self.take_btn.config(state=tk.DISABLED)

        # Check image progress if taking images
        if self._taking_images and state.camera_streaming:
            self._check_image_progress(state.camera_frames_captured)

        # Exposure - only update if not focused on the entry
        try:
            if state.camera_exposure and self.root.focus_get() != self.exposure_entry:
                # Convert from seconds to current unit
                exp_sec = state.camera_exposure
                unit = self.exposure_unit_var.get()

                if unit == "ms":
                    new_val = exp_sec * 1000.0
                elif unit == "s":
                    new_val = exp_sec
                elif unit == "min":
                    new_val = exp_sec / 60.0
                else:
                    new_val = exp_sec * 1000.0  # Default to ms

                # Display with full precision
                new_val_str = str(new_val)

                current = self.exposure_var.get()
                if current != new_val_str:
                    self.exposure_var.set(new_val_str)
        except (tk.TclError, AttributeError):
            # Widget is being destroyed or window is closing
            pass

        # Save checkbox visual feedback - highlight when actively saving
        if state.is_saving:
            self.save_checkbox.config(style='Active.TCheckbutton')
        else:
            self.save_checkbox.config(style='TCheckbutton')

        # Stats
        self.frames_saved_var.set(str(state.frames_saved))
        self.cubes_saved_var.set(str(state.cubes_saved))
        self.frames_dropped_var.set(str(state.frames_dropped))

        # Highlight drops in red
        if state.frames_dropped > 0:
            self.dropped_label.config(foreground="red")
        else:
            self.dropped_label.config(foreground="black")

    @property
    def root(self):
        """Get the root window."""
        return self.winfo_toplevel()
