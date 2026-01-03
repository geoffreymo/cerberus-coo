# gui/panels/output_controls.py
"""Output controls panel for Cerberus GUI."""

import os
import tkinter as tk
from tkinter import ttk, filedialog
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...api import CerberusAPI


class OutputControlsPanel(ttk.LabelFrame):
    """
    Panel for output/save controls.

    Includes object name, output directory, frames per cube, and save toggle.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent, text="Output Controls", padding=5)
        self.api = api

        # Variables
        self.object_name_var = tk.StringVar(value="Object")
        self.output_dir_var = tk.StringVar(value=os.getcwd())
        self.frames_per_cube_var = tk.StringVar(value="1000")
        self.is_saving_var = tk.BooleanVar(value=False)

        # Stats
        self.frames_saved_var = tk.StringVar(value="0")
        self.cubes_saved_var = tk.StringVar(value="0")
        self.frames_dropped_var = tk.StringVar(value="0")

        self._create_widgets()

    def _create_widgets(self):
        """Create panel widgets."""
        # Object name
        obj_frame = ttk.Frame(self)
        obj_frame.pack(fill=tk.X, pady=2)

        ttk.Label(obj_frame, text="Object:").pack(side=tk.LEFT)
        ttk.Entry(
            obj_frame, textvariable=self.object_name_var, width=20
        ).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        # Output directory
        dir_frame = ttk.Frame(self)
        dir_frame.pack(fill=tk.X, pady=2)

        ttk.Label(dir_frame, text="Directory:").pack(side=tk.LEFT)
        ttk.Entry(
            dir_frame, textvariable=self.output_dir_var, width=30
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

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # Save controls
        save_frame = ttk.Frame(self)
        save_frame.pack(fill=tk.X, pady=2)

        self.save_btn = ttk.Button(
            save_frame, text="Start Saving", command=self._toggle_save
        )
        self.save_btn.pack(side=tk.LEFT, padx=2)

        # Status
        self.status_label = ttk.Label(save_frame, text="")
        self.status_label.pack(side=tk.LEFT, padx=10)

        # Stats row
        stats_frame = ttk.Frame(self)
        stats_frame.pack(fill=tk.X, pady=2)

        ttk.Label(stats_frame, text="Frames:").pack(side=tk.LEFT)
        ttk.Label(stats_frame, textvariable=self.frames_saved_var, width=10).pack(side=tk.LEFT)

        ttk.Label(stats_frame, text="Cubes:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(stats_frame, textvariable=self.cubes_saved_var, width=5).pack(side=tk.LEFT)

        ttk.Label(stats_frame, text="Dropped:").pack(side=tk.LEFT, padx=(10, 0))
        self.dropped_label = ttk.Label(stats_frame, textvariable=self.frames_dropped_var, width=8)
        self.dropped_label.pack(side=tk.LEFT)

    def _browse_directory(self):
        """Open directory browser."""
        directory = filedialog.askdirectory(
            initialdir=self.output_dir_var.get(),
            title="Select Output Directory"
        )
        if directory:
            self.output_dir_var.set(directory)

    def _toggle_save(self):
        """Toggle save state."""
        if self.api.state.is_saving:
            self.api.stop_saving()
            self.save_btn.config(text="Start Saving")
            self.status_label.config(text="Stopped", foreground="black")
        else:
            if not self.api.state.camera_streaming:
                self.status_label.config(text="Not streaming!", foreground="red")
                return

            try:
                frames_per_cube = int(self.frames_per_cube_var.get())
            except ValueError:
                frames_per_cube = 1000

            success = self.api.start_saving(
                object_name=self.object_name_var.get(),
                output_dir=self.output_dir_var.get(),
                frames_per_cube=frames_per_cube
            )

            if success:
                self.save_btn.config(text="Stop Saving")
                self.status_label.config(text="Saving...", foreground="green")
            else:
                self.status_label.config(text="Failed to start", foreground="red")

    def update_from_state(self, state):
        """Update panel from system state."""
        if state.is_saving:
            self.save_btn.config(text="Stop Saving")
            self.status_label.config(text="Saving...", foreground="green")
        else:
            self.save_btn.config(text="Start Saving")

        self.frames_saved_var.set(str(state.frames_saved))
        self.cubes_saved_var.set(str(state.cubes_saved))
        self.frames_dropped_var.set(str(state.frames_dropped))

        # Highlight drops in red
        if state.frames_dropped > 0:
            self.dropped_label.config(foreground="red")
        else:
            self.dropped_label.config(foreground="black")
