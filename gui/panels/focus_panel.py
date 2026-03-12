# gui/panels/focus_panel.py
"""Focus loop controls panel for Cerberus GUI."""

import logging
import tkinter as tk
from tkinter import ttk
import threading
from typing import TYPE_CHECKING, List

from ..focus_window import get_filter_exposure_multiplier

if TYPE_CHECKING:
    from ...api import CerberusAPI

logger = logging.getLogger(__name__)


class FocusPanel(ttk.LabelFrame):
    """
    Panel for focus loop controls.

    Allows configuring and running automated focus sequences,
    including multi-filter focus runs.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent, text="Focus Loop", padding=5)
        self.api = api
        self._focus_thread = None

        # Variables
        self.start_pos_var = tk.StringVar(value="30.0")
        self.end_pos_var = tk.StringVar(value="45.0")
        self.step_var = tk.StringVar(value="0.25")
        self.exposure_var = tk.StringVar(value="5.0")
        self.progress_var = tk.StringVar(value="Idle")

        # Filter checkboxes state
        self.filter_vars = {}
        self._last_filters = []  # Track filter list to avoid unnecessary updates

        self._create_widgets()

    def _create_widgets(self):
        """Create panel widgets."""
        # Focus range
        range_frame = ttk.Frame(self)
        range_frame.pack(fill=tk.X, pady=2)

        ttk.Label(range_frame, text="Start (mm):").pack(side=tk.LEFT)
        ttk.Entry(
            range_frame, textvariable=self.start_pos_var, width=6
        ).pack(side=tk.LEFT, padx=2)

        ttk.Label(range_frame, text="End:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Entry(
            range_frame, textvariable=self.end_pos_var, width=6
        ).pack(side=tk.LEFT, padx=2)

        # Step size and exposure
        settings_frame = ttk.Frame(self)
        settings_frame.pack(fill=tk.X, pady=2)

        ttk.Label(settings_frame, text="Step (mm):").pack(side=tk.LEFT)
        ttk.Entry(
            settings_frame, textvariable=self.step_var, width=5
        ).pack(side=tk.LEFT, padx=2)

        ttk.Label(settings_frame, text="Exp (s):").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Entry(
            settings_frame, textvariable=self.exposure_var, width=5
        ).pack(side=tk.LEFT, padx=2)

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # Filter selection
        filter_label_frame = ttk.Frame(self)
        filter_label_frame.pack(fill=tk.X, pady=2)
        ttk.Label(filter_label_frame, text="Filters:").pack(side=tk.LEFT)

        self.filter_frame = ttk.Frame(self)
        self.filter_frame.pack(fill=tk.X, pady=2)

        # Placeholder - will be populated when filterwheel connects
        self.no_filters_label = ttk.Label(
            self.filter_frame, text="(connect filterwheel)", foreground="gray"
        )
        self.no_filters_label.pack(side=tk.LEFT)

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # Progress
        progress_frame = ttk.Frame(self)
        progress_frame.pack(fill=tk.X, pady=2)

        ttk.Label(progress_frame, text="Status:").pack(side=tk.LEFT)
        self.progress_label = ttk.Label(
            progress_frame, textvariable=self.progress_var, width=30
        )
        self.progress_label.pack(side=tk.LEFT, padx=5)

        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=5)

        self.start_btn = ttk.Button(
            btn_frame, text="Run Focus Loop", command=self._on_start
        )
        self.start_btn.pack(side=tk.LEFT, padx=2)

        self.abort_btn = ttk.Button(
            btn_frame, text="Abort", command=self._on_abort, state=tk.DISABLED
        )
        self.abort_btn.pack(side=tk.LEFT, padx=2)

    def _update_filter_checkboxes(self, filters: List[str]):
        """Update filter checkboxes based on available filters."""
        # Clear existing
        for widget in self.filter_frame.winfo_children():
            widget.destroy()
        self.filter_vars.clear()

        if not filters:
            self.no_filters_label = ttk.Label(
                self.filter_frame, text="(connect filterwheel)", foreground="gray"
            )
            self.no_filters_label.pack(side=tk.LEFT)
            return

        # Create checkbox for each filter
        for filter_name in filters:
            var = tk.BooleanVar(value=False)
            self.filter_vars[filter_name] = var
            cb = ttk.Checkbutton(
                self.filter_frame, text=filter_name, variable=var
            )
            cb.pack(side=tk.LEFT, padx=3)

    def _get_selected_filters(self) -> List[str]:
        """Get list of selected filter names."""
        return [name for name, var in self.filter_vars.items() if var.get()]

    def _on_start(self):
        """Handle start button click."""
        # Validate requirements
        if not self.api.state.camera_connected:
            self.progress_var.set("Error: Camera not connected")
            return
        if not self.api.state.telescope_connected:
            self.progress_var.set("Error: Telescope not connected")
            return
        if self.api.state.camera_streaming:
            self.progress_var.set("Error: Stop streaming first")
            return

        # Get parameters
        try:
            start = float(self.start_pos_var.get())
            end = float(self.end_pos_var.get())
            step = float(self.step_var.get())
            exposure = float(self.exposure_var.get())
        except ValueError:
            self.progress_var.set("Error: Invalid parameters")
            return

        filters = self._get_selected_filters()

        # Validate filter selection if filterwheel is connected
        if self.api.state.filterwheel_connected and not filters:
            self.progress_var.set("Error: No filters selected")
            return

        # Update UI
        self.start_btn.config(state=tk.DISABLED)
        self.abort_btn.config(state=tk.NORMAL)
        self.progress_var.set("Starting focus loop...")

        # Run in background thread
        self._focus_thread = threading.Thread(
            target=self._run_focus_loop,
            args=(start, end, step, exposure, filters),
            daemon=True
        )
        self._focus_thread.start()

    def _run_focus_loop(self, start: float, end: float, step: float,
                        exposure: float, filters: List[str]):
        """Run focus loop in background thread."""
        try:
            import time
            from ...focusloop import FocusLoopConfig

            # Create date-based directory structure matching regular captures
            date_str = time.strftime('%Y_%m_%d')
            output_dir = f"/data/cerberus/captures_{date_str}/focus"

            # Create filter-specific exposure times using multipliers
            filter_exposures = {}
            for filter_name in filters:
                multiplier = get_filter_exposure_multiplier(filter_name)
                filter_exposures[filter_name] = exposure * multiplier
                logger.info(f"Filter {filter_name}: {exposure}s × {multiplier} = {filter_exposures[filter_name]}s")

            config = FocusLoopConfig(
                start_position=start,
                end_position=end,
                step_size=step,
                exposure_time=exposure,
                filter_exposures=filter_exposures,
                filters=filters,
                output_dir=output_dir
            )

            # Progress callback - MUST use after() for thread safety with Tkinter
            def on_progress(progress):
                # Schedule GUI update in main thread
                self.after(0, lambda msg=progress.message: self.progress_var.set(msg))

            results = self.api.run_focus_loop(config=config, on_progress=on_progress)

            if results:
                # Show results
                if len(results) == 1:
                    result = list(results.values())[0]
                    if result.success:
                        self.progress_var.set(
                            f"Done: {result.best_focus:.2f}mm, "
                            f"FWHM={result.best_fwhm_arcsec:.2f}\""
                        )
                    else:
                        self.progress_var.set(f"Failed: {result.error_message}")
                else:
                    # Multi-filter summary
                    success_count = sum(1 for r in results.values() if r.success)
                    self.progress_var.set(
                        f"Done: {success_count}/{len(results)} filters successful"
                    )
            else:
                self.progress_var.set("Focus loop failed")

        except Exception as e:
            self.progress_var.set(f"Error: {e}")

        finally:
            # Re-enable buttons (in main thread)
            self.after(0, self._focus_complete)

    def _focus_complete(self):
        """Called when focus loop completes."""
        self.start_btn.config(state=tk.NORMAL)
        self.abort_btn.config(state=tk.DISABLED)

    def _on_abort(self):
        """Handle abort button click."""
        self.api.abort_focus_loop()
        self.progress_var.set("Aborting...")

    def update_from_state(self, state):
        """Update panel from system state."""
        # Update filter checkboxes only when filter list changes
        if state.filterwheel_connected and state.available_filters:
            if state.available_filters != self._last_filters:
                self._update_filter_checkboxes(state.available_filters)
                self._last_filters = state.available_filters[:]  # Make a copy
        elif not state.filterwheel_connected:
            if self._last_filters:  # Only clear if we had filters before
                self._update_filter_checkboxes([])
                self._last_filters = []

        # Update button states based on focus loop status
        if state.focus_loop_running:
            self.start_btn.config(state=tk.DISABLED)
            self.abort_btn.config(state=tk.NORMAL)
        else:
            self.start_btn.config(state=tk.NORMAL)
            self.abort_btn.config(state=tk.DISABLED)
