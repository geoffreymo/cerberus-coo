# gui/focus_window.py
"""Focus loop window for Cerberus GUI."""

import os
import logging
import tkinter as tk
from tkinter import ttk
import threading
import numpy as np
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from ..api import CerberusAPI

logger = logging.getLogger(__name__)

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


# =============================================================================
# Simulation Mode - Mock Hardware
# =============================================================================

def make_multi_star_image(
    size: tuple = (512, 512),
    n_stars: int = 8,
    fwhm_pixels: float = 10.0,
    background: float = 200,
    noise_std: float = 20,
    seed: int = None
) -> np.ndarray:
    """Generate image with multiple Gaussian stars."""
    if seed is not None:
        np.random.seed(seed)

    height, width = size
    image = background + np.random.normal(0, noise_std, size)
    sigma = fwhm_pixels / 2.355
    y, x = np.ogrid[:height, :width]

    for i in range(n_stars):
        margin = int(5 * fwhm_pixels)
        cy = np.random.randint(margin, height - margin)
        cx = np.random.randint(margin, width - margin)
        peak_flux = np.random.uniform(20000, 55000)
        r2 = (x - cx)**2 + (y - cy)**2
        star = peak_flux * np.exp(-r2 / (2 * sigma**2))
        image += star

    return np.clip(image, 0, 65535).astype(np.uint16)


def focus_to_fwhm(focus_position: float, optimal_focus: float = 37.5,
                  min_fwhm: float = 8.0, defocus_coeff: float = 0.15) -> float:
    """
    Convert focus position to expected FWHM (parabolic relationship).

    Parameters tuned so:
    - min_fwhm=8.0 px (~0.4") at optimal focus (above SExtractor's 5px threshold)
    - defocus_coeff=0.15 gives reasonable spread (~15px at ±7mm defocus)
    """
    defocus = focus_position - optimal_focus
    return min_fwhm + defocus_coeff * defocus**2


@dataclass
class MockTelescopeStatus:
    focus_mm: float = 37.5
    ra: str = "12:34:56.7"
    dec: str = "+45:30:00.0"
    ha: str = "-01:23:45"
    airmass: float = 1.2


class MockTelescope:
    """Mock telescope for simulation mode."""

    def __init__(self, initial_focus: float = 37.5):
        self.focus_mm = initial_focus

    def set_focus(self, position: float) -> bool:
        """Simulate focus move. Returns True on success (like real TCS)."""
        logger.info(f"[SIM] Moving focus: {self.focus_mm:.2f} -> {position:.2f} mm")
        self.focus_mm = position
        return True  # Simulate successful TCS response

    def get_focus(self) -> float:
        """Get current focus position."""
        return self.focus_mm

    def wait_for_focus(self, target_mm: float, tolerance_mm: float = 0.1,
                       timeout_sec: float = 60.0, poll_interval: float = 0.5) -> bool:
        """Simulate waiting for focus (instant in simulation)."""
        logger.info(f"[SIM] Focus at target: {self.focus_mm:.2f} mm")
        return True

    def get_status(self) -> MockTelescopeStatus:
        return MockTelescopeStatus(focus_mm=self.focus_mm)


class MockCamera:
    """Mock camera that generates synthetic star images."""

    def __init__(self, image_size: tuple = (512, 512), optimal_focus: float = 37.5, n_stars: int = 10):
        self.image_size = image_size
        self.optimal_focus = optimal_focus
        self.n_stars = n_stars
        self.exposure_time = 5.0
        self._telescope: Optional[MockTelescope] = None

    def set_telescope(self, telescope: MockTelescope):
        self._telescope = telescope

    def set_exposure(self, exposure: float):
        self.exposure_time = exposure

    def capture_single(self, timeout_ms: int = 30000) -> np.ndarray:
        focus = self.optimal_focus
        if self._telescope:
            focus = self._telescope.focus_mm
        fwhm = focus_to_fwhm(focus, self.optimal_focus)
        logger.info(f"[SIM] Capturing at focus {focus:.2f} mm, FWHM = {fwhm:.2f} px")
        return make_multi_star_image(
            size=self.image_size, n_stars=self.n_stars, fwhm_pixels=fwhm,
            background=200, noise_std=15, seed=int(focus * 100)
        )

    def save_fits(self, frame: np.ndarray, filepath: str, header_extra: dict = None):
        from astropy.io import fits
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        hdu = fits.PrimaryHDU(data=frame)
        if header_extra:
            for key, value in header_extra.items():
                try:
                    hdu.header[key] = value
                except:
                    pass
        hdu.writeto(filepath, overwrite=True)
        logger.info(f"[SIM] Saved {filepath}")


class MockFilterWheel:
    """Mock filter wheel for simulation mode."""

    def __init__(self, filters: List[str] = None):
        self.filters = filters or ['Clear', 'R', 'G', 'I']
        self.current_filter = self.filters[0]

    @property
    def filter(self) -> str:
        return self.current_filter

    def goto(self, filter_name: str):
        if filter_name not in self.filters:
            raise ValueError(f"Unknown filter: {filter_name}")
        logger.info(f"[SIM] Moving to filter: {filter_name}")
        self.current_filter = filter_name

    def wait_for_move(self, timeout: float = 30.0):
        pass


class FocusWindow(tk.Toplevel):
    """
    Separate window for focus loop controls.

    Allows configuring and running automated focus sequences,
    including multi-filter focus runs with exposure multipliers.
    """

    def __init__(self, parent, api: 'CerberusAPI', enable_simulation: bool = False):
        super().__init__(parent)
        self.title("Focus Loop")
        self.api = api
        self._focus_thread = None
        self.enable_simulation = enable_simulation

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

        # Simulation mode
        self.simulate_var = tk.BooleanVar(value=False)
        self.optimal_focus_var = tk.StringVar(value="37.5")  # For simulation

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

        # Simulation mode (only shown with --sim flag)
        if self.enable_simulation:
            sim_frame = ttk.LabelFrame(main_frame, text="Simulation Mode", padding=5)
            sim_frame.pack(fill=tk.X, pady=(0, 5))

            sim_row1 = ttk.Frame(sim_frame)
            sim_row1.pack(fill=tk.X, pady=2)
            ttk.Checkbutton(
                sim_row1, text="Enable Simulation (no real hardware)",
                variable=self.simulate_var, command=self._on_simulate_toggle
            ).pack(side=tk.LEFT)

            sim_row2 = ttk.Frame(sim_frame)
            sim_row2.pack(fill=tk.X, pady=2)
            ttk.Label(sim_row2, text="Optimal Focus:").pack(side=tk.LEFT)
            self.optimal_focus_entry = ttk.Entry(sim_row2, textvariable=self.optimal_focus_var, width=8, state=tk.DISABLED)
            self.optimal_focus_entry.pack(side=tk.LEFT, padx=5)
            ttk.Label(sim_row2, text="mm (simulated best focus)").pack(side=tk.LEFT)

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

    def _on_simulate_toggle(self):
        """Handle simulation mode toggle."""
        if self.simulate_var.get():
            if hasattr(self, 'optimal_focus_entry'):
                self.optimal_focus_entry.config(state=tk.NORMAL)
            # In simulation mode, populate filters if none available
            if not self.filter_vars:
                self._update_filter_checkboxes(['Clear', 'R', 'G', 'I'])
        else:
            if hasattr(self, 'optimal_focus_entry'):
                self.optimal_focus_entry.config(state=tk.DISABLED)
            # Restore real filter state
            self.update_from_state(self.api.state)

    def _on_start(self):
        """Handle start button click."""
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

        # Check if simulation mode
        if self.simulate_var.get():
            # Simulation mode - no real hardware needed
            try:
                optimal_focus = float(self.optimal_focus_var.get())
            except ValueError:
                self.progress_var.set("Error: Invalid optimal focus")
                return

            # Update UI
            self.start_btn.config(state=tk.DISABLED)
            self.abort_btn.config(state=tk.NORMAL)
            self.progress_var.set("Starting simulated focus loop...")

            # Run simulation in background thread
            self._focus_thread = threading.Thread(
                target=self._run_simulated_focus_loop,
                args=(start, end, step, base_exposure_ms, filters, optimal_focus),
                daemon=True
            )
            self._focus_thread.start()
            return

        # Real hardware mode - validate requirements
        if not self.api.state.camera_connected:
            self.progress_var.set("Error: Camera not connected")
            return
        if not self.api.state.telescope_connected:
            self.progress_var.set("Error: Telescope not connected")
            return
        if self.api.state.camera_streaming:
            self.progress_var.set("Error: Stop streaming first")
            return

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

    def _run_simulated_focus_loop(self, start: float, end: float, step: float,
                                   base_exposure_ms: float, filters: List[str],
                                   optimal_focus: float):
        """Run focus loop with simulated hardware."""
        try:
            import time
            from ..focusloop import FocusLoop, FocusLoopConfig

            # Create date-based directory structure
            date_str = time.strftime('%Y_%m_%d')
            output_dir = f"/tmp/cerberus_focus_sim_{date_str}"

            # Convert base exposure to seconds
            base_exposure_sec = base_exposure_ms / 1000.0

            # Create filter-specific exposure times
            filter_exposures = {}
            for filter_name in filters:
                multiplier = FILTER_EXPOSURE_MULTIPLIERS.get(filter_name, 1.0)
                filter_exposures[filter_name] = base_exposure_sec * multiplier

            config = FocusLoopConfig(
                start_position=start,
                end_position=end,
                step_size=step,
                exposure_time=base_exposure_sec,
                filter_exposures=filter_exposures,
                filters=filters if filters else [],
                output_dir=output_dir,
                settle_time=0.1,  # Fast for simulation
                auto_apply_best=False
            )

            # Create mock telescope and camera (simulated)
            mock_telescope = MockTelescope(initial_focus=optimal_focus)
            mock_camera = MockCamera(
                image_size=(512, 512),
                optimal_focus=optimal_focus,
                n_stars=10
            )
            mock_camera.set_telescope(mock_telescope)

            # Use REAL filterwheel if connected (only telescope/camera are simulated)
            real_filterwheel = None
            if filters and self.api.state.filterwheel_connected:
                real_filterwheel = self.api.filterwheel
                logger.info(f"[SIM] Using REAL filterwheel for simulation")
            elif filters:
                logger.warning(f"[SIM] Filterwheel not connected - filter changes will be skipped")

            # Create focus loop with mock telescope/camera but real filterwheel
            focus_loop = FocusLoop(
                camera=mock_camera,
                telescope=mock_telescope,
                filterwheel=real_filterwheel,
                config=config
            )

            # Progress callback
            def on_progress(progress):
                filter_str = f" [{progress.current_filter}]" if progress.current_filter else ""
                msg = f"[SIM]{filter_str} {progress.message}"
                self.after(0, lambda m=msg: self.progress_var.set(m))

            focus_loop.on_progress = on_progress

            # Run
            logger.info(f"[SIM] Starting simulated focus loop: {start}-{end}mm, step={step}")
            logger.info(f"[SIM] Optimal focus set to: {optimal_focus} mm")
            logger.info(f"[SIM] Filters: {filters or 'none'}")

            results = focus_loop.run()

            # Show results
            if results:
                for filter_name, result in results.items():
                    fname = filter_name or "single"
                    if result.success:
                        logger.info(f"[SIM] {fname}: Best focus = {result.best_focus:.2f} mm, "
                                   f"FWHM = {result.best_fwhm_arcsec:.3f}\"")
                    else:
                        logger.error(f"[SIM] {fname} failed: {result.error_message}")

                if len(results) == 1:
                    result = list(results.values())[0]
                    if result.success:
                        self.after(0, lambda: self.progress_var.set(
                            f"[SIM] Done: {result.best_focus:.2f}mm, "
                            f"FWHM={result.best_fwhm_arcsec:.3f}\""
                        ))
                    else:
                        self.after(0, lambda: self.progress_var.set(
                            f"[SIM] Failed: {result.error_message}"
                        ))
                else:
                    success_count = sum(1 for r in results.values() if r.success)
                    self.after(0, lambda: self.progress_var.set(
                        f"[SIM] Done: {success_count}/{len(results)} filters successful"
                    ))

                # Log output directory
                logger.info(f"[SIM] Output saved to: {output_dir}")
            else:
                self.after(0, lambda: self.progress_var.set("[SIM] Focus loop failed"))

        except Exception as e:
            logger.exception(f"[SIM] Focus loop error: {e}")
            self.after(0, lambda: self.progress_var.set(f"[SIM] Error: {e}"))

        finally:
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
            success = self.api.set_focus(focus)
            if not success:
                logger.error(f"TCS rejected focus command: {focus} mm")
        except ValueError:
            pass
        except Exception as e:
            logger.error(f"Failed to set focus: {e}")

    def _on_focus_offset(self, direction: int):
        """Handle focus offset button click."""
        if not self.api.state.telescope_connected:
            return

        try:
            offset = float(self.focus_offset_var.get()) * direction
            success = self.api.offset_focus(offset)
            if not success:
                logger.error(f"TCS rejected focus offset command: {offset:+.2f} mm")
        except ValueError:
            pass
        except Exception as e:
            logger.error(f"Failed to offset focus: {e}")

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
