# gui/focus_window.py
"""Focus loop window for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
import threading
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ..api import CerberusAPI


# Exposure multipliers for different filters
FILTER_EXPOSURE_MULTIPLIERS = {
    'Clear': 1.0,
    'R': 3.0,
    'G': 3.0,
    'I': 3.0,
    'U': 5.0,
    'Z': 5.0,
    'Ha': 10.0,
    'OIII': 10.0,
}


class FocusWindow(tk.Toplevel):
    """
    Separate window for focus loop controls.

    Allows configuring and running automated focus sequences,
    including multi-filter focus runs with exposure multipliers.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent)
        self.title("Focus Loop")
        self.api = api
        self._focus_thread = None

        # Variables
        self.start_pos_var = tk.StringVar(value="30.0")
        self.end_pos_var = tk.StringVar(value="45.0")
        self.step_var = tk.StringVar(value="2.5")
        self.base_exposure_var = tk.StringVar(value="100")  # Base exposure in ms
        self.progress_var = tk.StringVar(value="Idle")

        # Manual focus controls
        self.current_focus_var = tk.StringVar(value="--")
        self.focus_goto_var = tk.StringVar(value="35.0")
        self.focus_offset_var = tk.StringVar(value="0.5")

        # Filter checkboxes state
        self.filter_vars = {}
        self._last_filters = []  # Track filter list to avoid unnecessary updates

        self._create_widgets()

        # Update from current state
        self.update_from_state(api.state)

    def _create_widgets(self):
        """Create window widgets."""
        # Main frame with padding
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Manual Focus Controls
        manual_frame = ttk.LabelFrame(main_frame, text="Manual Focus Control", padding=5)
        manual_frame.pack(fill=tk.X, pady=(0, 5))

        # Current focus
        cur_frame = ttk.Frame(manual_frame)
        cur_frame.pack(fill=tk.X, pady=2)
        ttk.Label(cur_frame, text="Current:").pack(side=tk.LEFT)
        ttk.Label(cur_frame, textvariable=self.current_focus_var, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Label(cur_frame, text="mm").pack(side=tk.LEFT)

        # Go to focus
        goto_frame = ttk.Frame(manual_frame)
        goto_frame.pack(fill=tk.X, pady=2)
        ttk.Label(goto_frame, text="Go to:").pack(side=tk.LEFT)
        ttk.Entry(goto_frame, textvariable=self.focus_goto_var, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(goto_frame, text="mm").pack(side=tk.LEFT)
        ttk.Button(goto_frame, text="Go", command=self._on_focus_go, width=5).pack(side=tk.LEFT, padx=5)

        # Offset focus
        offset_frame = ttk.Frame(manual_frame)
        offset_frame.pack(fill=tk.X, pady=2)
        ttk.Label(offset_frame, text="Offset:").pack(side=tk.LEFT)
        ttk.Entry(offset_frame, textvariable=self.focus_offset_var, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(offset_frame, text="mm").pack(side=tk.LEFT)
        ttk.Button(offset_frame, text="-", command=lambda: self._on_focus_offset(-1), width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(offset_frame, text="+", command=lambda: self._on_focus_offset(1), width=3).pack(side=tk.LEFT, padx=2)

        # Focus range
        range_frame = ttk.LabelFrame(main_frame, text="Focus Range", padding=5)
        range_frame.pack(fill=tk.X, pady=(0, 5))

        row1 = ttk.Frame(range_frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="Start (mm):").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.start_pos_var, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row1, text="End (mm):").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Entry(row1, textvariable=self.end_pos_var, width=8).pack(side=tk.LEFT, padx=5)

        row2 = ttk.Frame(range_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="Step (mm):").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self.step_var, width=8).pack(side=tk.LEFT, padx=5)

        # Exposure settings
        exp_frame = ttk.LabelFrame(main_frame, text="Exposure", padding=5)
        exp_frame.pack(fill=tk.X, pady=(0, 5))

        row3 = ttk.Frame(exp_frame)
        row3.pack(fill=tk.X, pady=2)
        ttk.Label(row3, text="Base (Clear):").pack(side=tk.LEFT)
        ttk.Entry(row3, textvariable=self.base_exposure_var, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row3, text="ms").pack(side=tk.LEFT)

        # Multiplier info
        info_text = ttk.Label(
            exp_frame,
            text="Multipliers: R/G/I=3x, U/Z=5x, Ha/OIII=10x",
            font=("TkDefaultFont", 9),
            foreground="gray"
        )
        info_text.pack(anchor=tk.W, pady=(5, 0))

        # Filter selection
        filter_frame = ttk.LabelFrame(main_frame, text="Filters", padding=5)
        filter_frame.pack(fill=tk.X, pady=(0, 5))

        self.filter_container = ttk.Frame(filter_frame)
        self.filter_container.pack(fill=tk.X, pady=2)

        # Placeholder - will be populated when filterwheel connects
        self.no_filters_label = ttk.Label(
            self.filter_container, text="(connect filterwheel)", foreground="gray"
        )
        self.no_filters_label.pack(side=tk.LEFT)

        # Progress
        progress_frame = ttk.Frame(main_frame)
        progress_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(progress_frame, text="Status:").pack(side=tk.LEFT)
        self.progress_label = ttk.Label(
            progress_frame, textvariable=self.progress_var, width=40
        )
        self.progress_label.pack(side=tk.LEFT, padx=5)

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        self.start_btn = ttk.Button(
            btn_frame, text="Run Focus Loop", command=self._on_start
        )
        self.start_btn.pack(side=tk.LEFT, padx=2)

        self.abort_btn = ttk.Button(
            btn_frame, text="Abort", command=self._on_abort, state=tk.DISABLED
        )
        self.abort_btn.pack(side=tk.LEFT, padx=2)

        ttk.Button(
            btn_frame, text="Close", command=self.destroy
        ).pack(side=tk.RIGHT, padx=2)

    def _update_filter_checkboxes(self, filters: List[str]):
        """Update filter checkboxes based on available filters."""
        # Clear existing
        for widget in self.filter_container.winfo_children():
            widget.destroy()
        self.filter_vars.clear()

        if not filters:
            self.no_filters_label = ttk.Label(
                self.filter_container, text="(connect filterwheel)", foreground="gray"
            )
            self.no_filters_label.pack(side=tk.LEFT)
            return

        # Create checkbox for each filter, default to selected
        for filter_name in filters:
            var = tk.BooleanVar(value=True)  # Default to selected
            self.filter_vars[filter_name] = var
            cb = ttk.Checkbutton(
                self.filter_container, text=filter_name, variable=var
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
            base_exposure_ms = float(self.base_exposure_var.get())
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
            args=(start, end, step, base_exposure_ms, filters),
            daemon=True
        )
        self._focus_thread.start()

    def _run_focus_loop(self, start: float, end: float, step: float,
                        base_exposure_ms: float, filters: List[str]):
        """Run focus loop in background thread."""
        try:
            import time
            from ..focusloop import FocusLoopConfig

            # Create date-based directory structure matching regular captures
            date_str = time.strftime('%Y_%m_%d')
            output_dir = f"/data/cerberus/captures_{date_str}/focus"

            # Convert base exposure to seconds
            base_exposure_sec = base_exposure_ms / 1000.0

            # Create filter-specific exposure times using multipliers
            filter_exposures = {}
            for filter_name in filters:
                multiplier = FILTER_EXPOSURE_MULTIPLIERS.get(filter_name, 1.0)
                filter_exposures[filter_name] = base_exposure_sec * multiplier

            config = FocusLoopConfig(
                start_position=start,
                end_position=end,
                step_size=step,
                exposure_time=base_exposure_sec,  # For non-filter or default
                filter_exposures=filter_exposures,  # Per-filter exposures
                filters=filters,
                output_dir=output_dir
            )

            # Progress callback - MUST use after() for thread safety with Tkinter
            def on_progress(progress):
                # Schedule GUI update in main thread
                self.after(0, lambda msg=progress.message: self.progress_var.set(msg))

            # Run focus loop with filter-specific exposures
            # Note: We'll need to modify the API/FocusLoop to support per-filter exposures
            # For now, use base exposure
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

    def _on_focus_go(self):
        """Handle focus go button click."""
        if not self.api.state.telescope_connected:
            return

        try:
            focus = float(self.focus_goto_var.get())
            self.api.set_focus(focus)
        except ValueError:
            pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to set focus: {e}")

    def _on_focus_offset(self, direction: int):
        """Handle focus offset button click."""
        if not self.api.state.telescope_connected:
            return

        try:
            offset = float(self.focus_offset_var.get()) * direction
            self.api.offset_focus(offset)
        except ValueError:
            pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to offset focus: {e}")

    def update_from_state(self, state):
        """Update window from system state."""
        # Update current focus display
        if state.telescope_focus is not None:
            self.current_focus_var.set(f"{state.telescope_focus:.2f}")
        else:
            self.current_focus_var.set("--")

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
