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
        self.target_var = tk.StringVar(value="Object")
        self.comment_var = tk.StringVar(value="")
        self.filter_var = tk.StringVar(value="")
        self.exposure_var = tk.StringVar(value="100")
        self.exposure_unit_var = tk.StringVar(value="ms")
        self._last_unit = "ms"  # Track unit for conversions
        self.readout_var = tk.StringVar(value="Standard")

        # Variables - Take N Images
        self.n_images_var = tk.StringVar(value="")
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
        self.output_dir_var = tk.StringVar(value="/data/cerberus")
        self.frames_per_cube_var = tk.StringVar(value="1000")

        # Stats
        self.frames_captured_var = tk.StringVar(value="0")
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

        # Row 1: Target | Filter
        row1_frame = ttk.Frame(self)
        row1_frame.pack(fill=tk.X, pady=2)

        ttk.Label(row1_frame, text="Target:").pack(side=tk.LEFT)
        ttk.Entry(
            row1_frame, textvariable=self.target_var, width=15
        ).pack(side=tk.LEFT, padx=5)

        ttk.Label(row1_frame, text="Filter:").pack(side=tk.LEFT, padx=(10, 0))
        self.filter_combo = ttk.Combobox(
            row1_frame, textvariable=self.filter_var,
            width=12, state="readonly"
        )
        self.filter_combo.pack(side=tk.LEFT, padx=5)
        self.filter_combo.bind("<<ComboboxSelected>>", self._on_filter_change)

        # Row 2: Comment
        row2_frame = ttk.Frame(self)
        row2_frame.pack(fill=tk.X, pady=2)

        ttk.Label(row2_frame, text="Comment:").pack(side=tk.LEFT)
        ttk.Entry(
            row2_frame, textvariable=self.comment_var
        ).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # Row 3: Exposure | N Frames
        row3_frame = ttk.Frame(self)
        row3_frame.pack(fill=tk.X, pady=2)

        ttk.Label(row3_frame, text="Exposure:").pack(side=tk.LEFT)
        self.exposure_entry = ttk.Entry(
            row3_frame, textvariable=self.exposure_var, width=8
        )
        self.exposure_entry.pack(side=tk.LEFT, padx=5)
        self.exposure_entry.bind('<Return>', self._on_exposure_change)

        self.exposure_unit_combo = ttk.Combobox(
            row3_frame, textvariable=self.exposure_unit_var,
            width=5, state="readonly", values=["ms", "s", "min"]
        )
        self.exposure_unit_combo.pack(side=tk.LEFT, padx=2)
        self.exposure_unit_combo.bind("<<ComboboxSelected>>", self._on_unit_change)

        ttk.Label(row3_frame, text="N Frames:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Entry(
            row3_frame, textvariable=self.n_images_var, width=8
        ).pack(side=tk.LEFT, padx=5)

        # Row 4: Readout | Save checkbox | Start button
        row4_frame = ttk.Frame(self)
        row4_frame.pack(fill=tk.X, pady=2)

        ttk.Label(row4_frame, text="Readout:").pack(side=tk.LEFT)
        self.readout_combo = ttk.Combobox(
            row4_frame, textvariable=self.readout_var,
            width=12, state="readonly", values=["Ultra Quiet", "Standard"]
        )
        self.readout_combo.pack(side=tk.LEFT, padx=5)
        self.readout_combo.bind("<<ComboboxSelected>>", self._on_readout_change)

        self.save_checkbox = ttk.Checkbutton(
            row4_frame, text="Save", variable=self.save_var,
            command=self._on_save_toggle
        )
        self.save_checkbox.pack(side=tk.LEFT, padx=10)

        self.start_stop_btn = ttk.Button(
            row4_frame, text="Start", command=self._on_start_stop, state=tk.DISABLED
        )
        self.start_stop_btn.pack(side=tk.LEFT, padx=5)

        # Row 5: Streaming timer and frame counter
        row5_frame = ttk.Frame(self)
        row5_frame.pack(fill=tk.X, pady=2)

        self.stream_timer_label = ttk.Label(row5_frame, textvariable=self.stream_time_var)
        self.stream_timer_label.pack(side=tk.LEFT, padx=5)

        ttk.Label(row5_frame, text="Frames:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(row5_frame, textvariable=self.frames_captured_var).pack(side=tk.LEFT, padx=5)

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # Save Path
        dir_frame = ttk.Frame(self)
        dir_frame.pack(fill=tk.X, pady=2)

        ttk.Label(dir_frame, text="Save Path:").pack(side=tk.LEFT)
        ttk.Entry(
            dir_frame, textvariable=self.output_dir_var, width=20
        ).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(
            dir_frame, text="...", command=self._browse_directory, width=3
        ).pack(side=tk.LEFT)

        # Cube Size
        cube_frame = ttk.Frame(self)
        cube_frame.pack(fill=tk.X, pady=2)

        ttk.Label(cube_frame, text="Cube Size:").pack(side=tk.LEFT)
        ttk.Entry(
            cube_frame, textvariable=self.frames_per_cube_var, width=8
        ).pack(side=tk.LEFT, padx=5)
        ttk.Label(cube_frame, text="frames").pack(side=tk.LEFT)

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
            self.start_stop_btn.config(state=tk.DISABLED)
        else:
            if self.api.connect_camera():
                self.connect_btn.config(text="Disconnect")
                self.start_stop_btn.config(state=tk.NORMAL)
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

    def _on_start_stop(self, event=None):
        """Handle start/stop button click."""
        if self.api.state.camera_streaming:
            # Currently streaming - STOP
            # Stop saving first if active
            if self.api.state.is_saving:
                self.api.stop_saving()

            self.api.stop_streaming()
            self.start_stop_btn.config(text="Start")

            # Stop streaming timer
            self._stop_stream_timer()

            # Reset take images state if was running
            if self._taking_images:
                self._taking_images = False
                self.image_progress_var.set("")
        else:
            # Currently stopped - START
            # Check if N Frames is specified
            try:
                n_images = int(self.n_images_var.get()) if self.n_images_var.get() else 0
            except ValueError:
                n_images = 0

            if self.api.start_streaming():
                self.start_stop_btn.config(text="Stop")

                # Start streaming timer
                self._start_stream_timer()

                # Set up take images mode if N > 0
                if n_images > 0:
                    self._taking_images = True
                    self._target_frames = n_images
                    self._start_frame_count = self.api.state.camera_frames_captured
                    self.image_progress_var.set(f"Taking: 0 / {n_images}")

                # Auto-start saving if checkbox is checked
                if self.save_var.get():
                    self._start_saving()

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

    def _on_filter_change(self, event=None):
        """Handle filter selection change."""
        if not self.api.state.filterwheel_connected:
            return

        selected = self.filter_var.get()
        if selected:
            try:
                self.api.set_filter(selected)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to set filter: {e}")

    def _on_readout_change(self, event=None):
        """Handle readout mode change."""
        if not self.api.state.camera_connected:
            return

        try:
            value = self.readout_var.get()
            # READOUT_SPEED: 1.0 = Ultra Quiet, 2.0 = Standard
            speed_map = {"Ultra Quiet": 1.0, "Standard": 2.0}
            if value in speed_map:
                self.api.set_camera_property("READOUT_SPEED", speed_map[value])
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to set readout speed: {e}")

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

        object_name = self.target_var.get()
        output_dir = self.output_dir_var.get()
        comment = self.comment_var.get()

        logger.info(f"_start_saving called: object={object_name}, dir={output_dir}, frames={frames_per_cube}, comment={comment}")
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
            frames_per_cube=frames_per_cube,
            comment=comment
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
                    self.start_stop_btn.config(text="Start")
                    self._stop_stream_timer()

    def update_from_state(self, state):
        """Update panel from system state."""
        # Connection state
        if state.camera_connected:
            self.connect_btn.config(text="Disconnect")
            self.status_label.config(text="Connected", foreground="green")
            self.start_stop_btn.config(state=tk.NORMAL)

            # Update button text based on streaming state
            if state.camera_streaming:
                self.start_stop_btn.config(text="Stop")
            else:
                self.start_stop_btn.config(text="Start")
        else:
            self.connect_btn.config(text="Connect")
            self.status_label.config(text="Disconnected", foreground="black")
            self.start_stop_btn.config(state=tk.DISABLED)

        # Update filter combo if filterwheel connected
        if state.filterwheel_connected and state.available_filters:
            if self.filter_combo['values'] != tuple(state.available_filters):
                self.filter_combo['values'] = state.available_filters
            # Update current filter selection
            if state.current_filter and state.current_filter != self.filter_var.get():
                self.filter_var.set(state.current_filter)
        else:
            self.filter_combo['values'] = []
            self.filter_var.set("")

        # Update frames captured display
        self.frames_captured_var.set(str(state.camera_frames_captured))

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
