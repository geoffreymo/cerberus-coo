# gui/panels/image_display.py
"""Image display panel for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
import numpy as np
from typing import TYPE_CHECKING, Optional, Tuple, Callable
import time
import threading
import logging

logger = logging.getLogger(__name__)

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

if TYPE_CHECKING:
    from ...api import CerberusAPI


class ImageDisplayPanel(ttk.LabelFrame):
    """
    Panel for live image display.

    Uses OpenCV for efficient image scaling and display.
    Includes basic contrast controls and ROI selection via SHIFT+drag.
    """

    def __init__(self, parent, api: 'CerberusAPI', display_size: tuple = (640, 480)):
        super().__init__(parent, text="Live View", padding=5)
        self.api = api
        self.display_size = display_size

        # Display state
        self._window_name = "Cerberus Live View"
        self._running = False
        self._last_frame: Optional[np.ndarray] = None
        self._window_created = False
        self._display_lock = threading.Lock()  # Prevent display update pileup
        self._after_id = None  # Track scheduled after callback for cleanup

        # Scaling - defaults for typical dark/bias frames
        self.scale_min_var = tk.StringVar(value="200")
        self.scale_max_var = tk.StringVar(value="300")
        self.auto_scale_var = tk.BooleanVar(value=False)

        # Statistics
        self.fps_var = tk.StringVar(value="0.0")
        self.mean_var = tk.StringVar(value="0")
        self.max_var = tk.StringVar(value="0")
        self.cursor_var = tk.StringVar(value="--")

        # FPS calculation
        self._frame_count = 0
        self._fps_time = time.time()

        # Mouse tracking for cursor value
        self._mouse_x = 0
        self._mouse_y = 0

        # Scale factor for coordinate conversion (like v18)
        # When we scale up small frames for display, track the factor
        self._display_scale_factor = 1.0
        self._min_display_size = 512  # Minimum display dimension

        # ROI selection state (SHIFT+drag to select subwindow)
        self._roi_selection_mode = False
        self._roi_start_point: Optional[Tuple[int, int]] = None  # Image coordinates
        self._roi_end_point: Optional[Tuple[int, int]] = None    # Image coordinates
        self._roi_drag_active = False

        # Callback for ROI selection complete
        self.on_roi_selected: Optional[Callable[[int, int, int, int], None]] = None

        # Current subarray offset (for nested ROI selection)
        self._current_hpos = 0
        self._current_vpos = 0

        # FWHM tracking state - load from config
        from ...config import get_config
        config = get_config()
        self._fwhm_target: Optional[Tuple[int, int]] = None  # Image coordinates of tracked star
        self._fwhm_box_size = config.instrument.fwhm_box_size_pixels  # Size of cutout for FWHM measurement
        self._fwhm_value: Optional[float] = None  # Current FWHM in arcsec
        self._fwhm_history: list = []  # [(timestamp, fwhm_arcsec), ...]
        self._fwhm_history_max = 1000  # Max history entries
        self._plate_scale = config.instrument.plate_scale_arcsec_per_pixel
        self._fwhm_animation = None  # Matplotlib animation (kept alive to prevent GC)
        self._fwhm_plot_fig = None   # Matplotlib figure reference

        # Photometry state (aperture photometry for lightcurves)
        self._photometry_enabled = False
        self._target_aperture: Optional[Tuple[int, int]] = None  # (x, y) image coords
        self._comparison_aperture: Optional[Tuple[int, int]] = None  # (x, y) image coords
        self._aperture_radius = tk.IntVar(value=10)  # Aperture radius in pixels
        self._annulus_inner = tk.IntVar(value=15)    # Inner annulus radius
        self._annulus_outer = tk.IntVar(value=25)    # Outer annulus radius
        self._photometry_data: list = []  # [{time, target_flux, comp_flux, relative_flux}, ...]
        self._photometry_data_max = 10000  # Max data points to keep
        self._lightcurve_window = None
        self._lightcurve_animation = None
        self._lightcurve_fig = None
        self._last_photometry_time = 0  # Rate limiting

        # Guiding state
        self._guiding_config = config.guiding
        self._guiding_enabled = False
        self._guiding_calibrating = False
        self._guiding_reference: Optional[Tuple[float, float]] = None  # (x, y) reference position in pixels
        self._position_history: list = []  # [(timestamp, x_offset, y_offset), ...]
        self._last_correction_time = 0
        self._guiding_calibration_start = 0  # When calibration started

        self._create_widgets()

    def _create_widgets(self):
        """Create panel widgets."""
        # Info row 1
        info_frame = ttk.Frame(self)
        info_frame.pack(fill=tk.X, pady=2)

        ttk.Label(info_frame, text="FPS:").pack(side=tk.LEFT)
        ttk.Label(info_frame, textvariable=self.fps_var, width=6).pack(side=tk.LEFT)

        ttk.Label(info_frame, text="Mean:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(info_frame, textvariable=self.mean_var, width=8).pack(side=tk.LEFT)

        ttk.Label(info_frame, text="Max:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(info_frame, textvariable=self.max_var, width=8).pack(side=tk.LEFT)

        ttk.Label(info_frame, text="Cursor:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(info_frame, textvariable=self.cursor_var, width=8).pack(side=tk.LEFT)

        # FWHM display row
        fwhm_frame = ttk.Frame(self)
        fwhm_frame.pack(fill=tk.X, pady=2)

        ttk.Label(fwhm_frame, text="FWHM:").pack(side=tk.LEFT)
        self.fwhm_var = tk.StringVar(value="--")
        ttk.Label(fwhm_frame, textvariable=self.fwhm_var, width=12).pack(side=tk.LEFT)

        self.clear_fwhm_btn = ttk.Button(
            fwhm_frame, text="Clear", command=self._clear_fwhm_target,
            width=6
        )
        self.clear_fwhm_btn.pack(side=tk.LEFT, padx=(10, 0))

        self.plot_fwhm_btn = ttk.Button(
            fwhm_frame, text="Plot", command=self._show_fwhm_plot,
            width=6
        )
        self.plot_fwhm_btn.pack(side=tk.LEFT, padx=(5, 0))

        ttk.Label(
            fwhm_frame, text="(Right-click on star)",
            font=("TkDefaultFont", 9), foreground="gray"
        ).pack(side=tk.LEFT, padx=(10, 0))

        # Guiding controls
        guiding_frame = ttk.LabelFrame(self, text="Guiding", padding=3)
        guiding_frame.pack(fill=tk.X, pady=2)

        guiding_row = ttk.Frame(guiding_frame)
        guiding_row.pack(fill=tk.X)

        self._guiding_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            guiding_row, text="Enable", variable=self._guiding_enabled_var,
            command=self._toggle_guiding
        ).pack(side=tk.LEFT)

        self._guiding_status_var = tk.StringVar(value="Not guiding")
        ttk.Label(guiding_row, textvariable=self._guiding_status_var, width=22).pack(side=tk.LEFT, padx=(5, 0))

        ttk.Button(
            guiding_row, text="Reset", command=self._reset_guiding_reference, width=5
        ).pack(side=tk.LEFT, padx=(5, 0))

        # Photometry controls
        phot_frame = ttk.LabelFrame(self, text="Photometry", padding=3)
        phot_frame.pack(fill=tk.X, pady=2)

        # Row 1: Enable checkbox and status
        phot_row1 = ttk.Frame(phot_frame)
        phot_row1.pack(fill=tk.X)

        self._phot_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            phot_row1, text="Enable", variable=self._phot_enabled_var,
            command=self._toggle_photometry
        ).pack(side=tk.LEFT)

        self._target_status_var = tk.StringVar(value="T: --")
        ttk.Label(phot_row1, textvariable=self._target_status_var, width=12).pack(side=tk.LEFT, padx=(10, 0))

        self._comp_status_var = tk.StringVar(value="C: --")
        ttk.Label(phot_row1, textvariable=self._comp_status_var, width=12).pack(side=tk.LEFT)

        ttk.Button(
            phot_row1, text="Clear", command=self._clear_apertures, width=6
        ).pack(side=tk.LEFT, padx=(10, 0))

        ttk.Button(
            phot_row1, text="Plot", command=self._show_lightcurve, width=6
        ).pack(side=tk.LEFT, padx=(5, 0))

        # Row 2: Aperture settings
        phot_row2 = ttk.Frame(phot_frame)
        phot_row2.pack(fill=tk.X, pady=(2, 0))

        ttk.Label(phot_row2, text="Ap R:").pack(side=tk.LEFT)
        ttk.Spinbox(
            phot_row2, from_=3, to=50, width=4, textvariable=self._aperture_radius
        ).pack(side=tk.LEFT, padx=(2, 10))

        ttk.Label(phot_row2, text="Annulus:").pack(side=tk.LEFT)
        ttk.Spinbox(
            phot_row2, from_=5, to=100, width=4, textvariable=self._annulus_inner
        ).pack(side=tk.LEFT, padx=2)
        ttk.Label(phot_row2, text="-").pack(side=tk.LEFT)
        ttk.Spinbox(
            phot_row2, from_=10, to=150, width=4, textvariable=self._annulus_outer
        ).pack(side=tk.LEFT, padx=2)

        ttk.Label(
            phot_row2, text="(CTRL+click: target, ALT+click: comp)",
            font=("TkDefaultFont", 9), foreground="gray"
        ).pack(side=tk.LEFT, padx=(10, 0))

        # Scale controls
        scale_frame = ttk.Frame(self)
        scale_frame.pack(fill=tk.X, pady=2)

        ttk.Checkbutton(
            scale_frame, text="Auto", variable=self.auto_scale_var
        ).pack(side=tk.LEFT)

        ttk.Label(scale_frame, text="Min:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Entry(
            scale_frame, textvariable=self.scale_min_var, width=8
        ).pack(side=tk.LEFT, padx=2)

        ttk.Label(scale_frame, text="Max:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Entry(
            scale_frame, textvariable=self.scale_max_var, width=8
        ).pack(side=tk.LEFT, padx=2)

        # Display button
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=2)

        self.display_btn = ttk.Button(
            btn_frame, text="Open Display", command=self._toggle_display
        )
        self.display_btn.pack(side=tk.LEFT, padx=2)

    def _toggle_display(self):
        """Toggle display window."""
        if self._running:
            self._stop_display()
        else:
            self._start_display()

    def _start_display(self):
        """Start the display loop (runs in MAIN THREAD via self.after to avoid Qt deadlocks)."""
        if not CV2_AVAILABLE:
            return

        self._running = True
        self.display_btn.config(text="Close Display")

        # Start display loop in main thread (avoids Qt deadlock)
        self._after_id = self.after(20, self._display_loop)

    def open_display_next_to_window(self):
        """Open display and position it next to the main window."""
        if self._running:
            return  # Already running

        self._start_display()

        # Position window next to main window after it's created
        self.after(100, self._position_display_window)

    def _position_display_window(self):
        """Position the OpenCV window next to the main Tk window."""
        if not self._window_created or not CV2_AVAILABLE:
            return

        try:
            # Get main window geometry
            root = self.winfo_toplevel()
            root.update_idletasks()

            # Get main window position and size
            main_x = root.winfo_x()
            main_y = root.winfo_y()
            main_width = root.winfo_width()

            # Position OpenCV window to the right of main window
            display_x = main_x + main_width + 10
            display_y = main_y

            # Move the window
            cv2.moveWindow(self._window_name, display_x, display_y)

        except Exception:
            pass  # Ignore positioning errors

    def _stop_display(self):
        """Stop the display loop."""
        self._running = False

        if CV2_AVAILABLE and self._window_created:
            try:
                cv2.destroyWindow(self._window_name)
                self._window_created = False
            except:
                pass

        self.display_btn.config(text="Open Display")

    def _mouse_callback(self, event, x, y, flags, param):
        """OpenCV mouse callback for tracking cursor position and ROI selection."""
        # Convert display coordinates to image coordinates
        img_x, img_y = self._display_to_image_coords(x, y)

        # Check if SHIFT is held
        shift_held = (flags & cv2.EVENT_FLAG_SHIFTKEY) != 0

        if event == cv2.EVENT_MOUSEMOVE:
            self._mouse_x = img_x
            self._mouse_y = img_y
            # Update cursor value immediately on mouse move
            if self._last_frame is not None:
                self._update_cursor_value(self._last_frame)

            # Update ROI rectangle while dragging
            if self._roi_start_point is not None:
                if shift_held or self._roi_drag_active:
                    self._roi_end_point = (img_x, img_y)

        elif event == cv2.EVENT_LBUTTONDOWN:
            # Check modifier keys
            ctrl_held = (flags & cv2.EVENT_FLAG_CTRLKEY) != 0
            alt_held = (flags & cv2.EVENT_FLAG_ALTKEY) != 0

            if ctrl_held and self._photometry_enabled:
                # CTRL+click sets target aperture
                self._set_target_aperture(img_x, img_y)
            elif alt_held and self._photometry_enabled:
                # ALT+click sets comparison aperture
                self._set_comparison_aperture(img_x, img_y)
            elif shift_held:
                # Start ROI selection if SHIFT is held
                self._roi_start_point = (img_x, img_y)
                self._roi_end_point = (img_x, img_y)
                self._roi_drag_active = True
                logger.info(f"ROI selection started at ({img_x}, {img_y})")

        elif event == cv2.EVENT_LBUTTONUP:
            # Finish ROI selection
            if self._roi_start_point is not None and self._roi_drag_active:
                self._roi_end_point = (img_x, img_y)
                self._roi_drag_active = False
                self._finish_roi_selection()

        elif event == cv2.EVENT_RBUTTONDOWN:
            # Right-click to cancel ROI or set FWHM target
            if self._roi_drag_active:
                self._cancel_roi_selection()
            else:
                # Set FWHM tracking target at this position
                self._set_fwhm_target(img_x, img_y)

    def _display_to_image_coords(self, display_x: int, display_y: int) -> Tuple[int, int]:
        """Convert display window coordinates to image coordinates."""
        if self._last_frame is None:
            return display_x, display_y

        img_height, img_width = self._last_frame.shape[:2]

        # Convert display coords to image coords using tracked scale factor (like v18)
        # When we scale up small frames, we divide mouse coords by that factor
        scale = self._display_scale_factor
        img_x = int(display_x / scale)
        img_y = int(display_y / scale)

        # Clamp to image bounds
        img_x = max(0, min(img_x, img_width - 1))
        img_y = max(0, min(img_y, img_height - 1))

        return img_x, img_y

    def _finish_roi_selection(self):
        """Finish ROI selection and apply windowing."""
        if self._roi_start_point is None or self._roi_end_point is None:
            self._cancel_roi_selection()
            return

        x1, y1 = self._roi_start_point
        x2, y2 = self._roi_end_point

        # Ensure x1 < x2 and y1 < y2
        left = min(x1, x2)
        right = max(x1, x2)
        top = min(y1, y2)
        bottom = max(y1, y2)

        # Check minimum size
        if right - left < 16 or bottom - top < 16:
            logger.warning("ROI too small (min 16x16)")
            self._cancel_roi_selection()
            return

        # Convert to absolute sensor coordinates (add current subarray offset)
        abs_left = left + self._current_hpos
        abs_right = right + self._current_hpos
        abs_top = top + self._current_vpos
        abs_bottom = bottom + self._current_vpos

        # Round to nearest 4 pixels (camera requirement)
        hpos = (abs_left // 4) * 4
        vpos = (abs_top // 4) * 4
        hsize = ((abs_right - hpos + 3) // 4) * 4
        vsize = ((abs_bottom - vpos + 3) // 4) * 4

        logger.info(f"ROI selected: HPOS={hpos}, VPOS={vpos}, HSIZE={hsize}, VSIZE={vsize}")

        # Clear ROI state
        self._roi_start_point = None
        self._roi_end_point = None
        self._roi_drag_active = False

        # Call callback if set
        if self.on_roi_selected:
            self.on_roi_selected(hpos, vpos, hsize, vsize)

    def _cancel_roi_selection(self):
        """Cancel ROI selection."""
        self._roi_start_point = None
        self._roi_end_point = None
        self._roi_drag_active = False
        logger.info("ROI selection cancelled")

    def set_current_subarray_offset(self, hpos: int, vpos: int):
        """Set current subarray offset for nested ROI selection."""
        self._current_hpos = hpos
        self._current_vpos = vpos

    def _set_fwhm_target(self, img_x: int, img_y: int):
        """Set FWHM tracking target at given image coordinates."""
        if self._last_frame is None:
            return

        height, width = self._last_frame.shape[:2]
        half_box = self._fwhm_box_size // 2

        # Check bounds - need enough room for cutout
        if img_x < half_box or img_x >= width - half_box:
            logger.warning(f"FWHM target too close to horizontal edge")
            return
        if img_y < half_box or img_y >= height - half_box:
            logger.warning(f"FWHM target too close to vertical edge")
            return

        self._fwhm_target = (img_x, img_y)
        self._fwhm_history = []  # Clear history on new target
        logger.info(f"FWHM tracking target set at ({img_x}, {img_y})")

    def _clear_fwhm_target(self):
        """Clear FWHM tracking target."""
        self._fwhm_target = None
        self._fwhm_value = None
        self._fwhm_history = []
        self.fwhm_var.set("--")
        logger.info("FWHM tracking cleared")

    def _measure_fwhm_gaussian(self, frame: np.ndarray) -> Tuple[Optional[float], Optional[Tuple[float, float]]]:
        """
        Measure FWHM and centroid using 2D Gaussian fit.

        Returns:
            Tuple of (fwhm_pixels, centroid_offset) where:
            - fwhm_pixels: FWHM in pixels, or None if fit fails
            - centroid_offset: (dx, dy) offset in pixels from target position, or None if fit fails
        """
        if self._fwhm_target is None or frame is None:
            return None, None

        try:
            from scipy.optimize import curve_fit
            from scipy.ndimage import center_of_mass
        except ImportError:
            logger.warning("scipy not available for Gaussian fitting")
            return None, None

        cx, cy = self._fwhm_target
        half_box = self._fwhm_box_size // 2

        # Extract cutout
        y1, y2 = cy - half_box, cy + half_box
        x1, x2 = cx - half_box, cx + half_box

        # Bounds check
        height, width = frame.shape[:2]
        if y1 < 0 or y2 > height or x1 < 0 or x2 > width:
            return None, None

        cutout = frame[y1:y2, x1:x2].astype(np.float64)

        # Subtract background (median of edge pixels)
        edge_pixels = np.concatenate([
            cutout[0, :], cutout[-1, :],
            cutout[1:-1, 0], cutout[1:-1, -1]
        ])
        background = np.median(edge_pixels)
        cutout = cutout - background

        # Find initial center using center of mass
        try:
            com_y, com_x = center_of_mass(np.maximum(cutout, 0))
            if np.isnan(com_x) or np.isnan(com_y):
                com_x, com_y = half_box, half_box
        except:
            com_x, com_y = half_box, half_box

        # Create coordinate grids
        y_grid, x_grid = np.mgrid[0:cutout.shape[0], 0:cutout.shape[1]]

        # 2D Gaussian function
        def gaussian_2d(coords, amplitude, x0, y0, sigma_x, sigma_y, offset):
            x, y = coords
            return (offset + amplitude * np.exp(
                -((x - x0)**2 / (2 * sigma_x**2) + (y - y0)**2 / (2 * sigma_y**2))
            )).ravel()

        # Initial parameter guesses
        amplitude_guess = np.max(cutout) - np.min(cutout)
        sigma_guess = 5.0  # ~10 pixel FWHM initial guess

        p0 = [amplitude_guess, com_x, com_y, sigma_guess, sigma_guess, 0]

        # Bounds for parameters
        bounds = (
            [0, 0, 0, 1, 1, -np.inf],  # lower bounds
            [np.inf, cutout.shape[1], cutout.shape[0], half_box, half_box, np.inf]  # upper
        )

        try:
            popt, _ = curve_fit(
                gaussian_2d,
                (x_grid, y_grid),
                cutout.ravel(),
                p0=p0,
                bounds=bounds,
                maxfev=1000
            )

            amplitude, x0, y0, sigma_x, sigma_y = popt[:5]

            # FWHM = 2 * sqrt(2 * ln(2)) * sigma ≈ 2.355 * sigma
            fwhm_x = 2.355 * sigma_x
            fwhm_y = 2.355 * sigma_y

            # Use geometric mean of x and y FWHM
            fwhm_pixels = np.sqrt(fwhm_x * fwhm_y)

            # Sanity checks
            if fwhm_pixels < 1 or fwhm_pixels > self._fwhm_box_size:
                return None, None

            # Centroid offset from target (in pixels)
            # x0, y0 are in cutout coordinates (0 to box_size)
            # half_box is the center of the cutout
            centroid_offset = (float(x0 - half_box), float(y0 - half_box))

            return float(fwhm_pixels), centroid_offset

        except Exception as e:
            logger.debug(f"Gaussian fit failed: {e}")
            return None, None

    def _update_fwhm(self, frame: np.ndarray):
        """Update FWHM measurement and history."""
        if self._fwhm_target is None:
            return

        fwhm_pixels, centroid_offset = self._measure_fwhm_gaussian(frame)

        if fwhm_pixels is not None and centroid_offset is not None:
            fwhm_arcsec = fwhm_pixels * self._plate_scale
            self._fwhm_value = fwhm_arcsec

            # Update FWHM history
            timestamp = time.time()
            self._fwhm_history.append((timestamp, fwhm_arcsec))

            # Trim history if too long
            if len(self._fwhm_history) > self._fwhm_history_max:
                self._fwhm_history = self._fwhm_history[-self._fwhm_history_max:]

            # Update position history for guiding
            self._position_history.append((timestamp, centroid_offset[0], centroid_offset[1]))

            # Trim position history (keep 2x averaging window worth)
            max_history_seconds = self._guiding_config.averaging_window_seconds * 2
            cutoff_time = timestamp - max_history_seconds
            self._position_history = [
                (t, x, y) for t, x, y in self._position_history if t >= cutoff_time
            ]

            # Update display
            self.fwhm_var.set(f"{fwhm_arcsec:.3f}\"")
        else:
            self.fwhm_var.set("fit err")

    def _show_fwhm_plot(self):
        """Show FWHM history plot in a separate window."""
        if not self._fwhm_history:
            logger.info("No FWHM history to plot")
            return

        try:
            import matplotlib
            matplotlib.use('TkAgg')  # Use Tk backend for GUI window
            import matplotlib.pyplot as plt
            from matplotlib.animation import FuncAnimation
        except ImportError:
            logger.warning("matplotlib not available for FWHM plotting")
            return

        # Create figure
        fig, ax = plt.subplots(figsize=(8, 4))
        fig.canvas.manager.set_window_title("FWHM History")

        # Extract data
        timestamps = [t for t, _ in self._fwhm_history]
        fwhms = [f for _, f in self._fwhm_history]

        # Convert to relative time (seconds from start)
        t0 = timestamps[0]
        rel_times = [t - t0 for t in timestamps]

        # Plot
        line, = ax.plot(rel_times, fwhms, 'b-', linewidth=1, marker='o', markersize=2)
        ax.set_xlabel("Time (seconds)", fontsize=10)
        ax.set_ylabel("FWHM (arcsec)", fontsize=10)
        ax.set_title("Live FWHM Tracking", fontsize=12)
        ax.grid(True, alpha=0.3)

        # Add statistics text
        if fwhms:
            mean_fwhm = np.mean(fwhms)
            std_fwhm = np.std(fwhms)
            min_fwhm = np.min(fwhms)
            max_fwhm = np.max(fwhms)
            stats_text = f"Mean: {mean_fwhm:.3f}\"  Std: {std_fwhm:.3f}\"  Min: {min_fwhm:.3f}\"  Max: {max_fwhm:.3f}\""
            ax.set_title(f"Live FWHM Tracking\n{stats_text}", fontsize=10)

        # Animation update function for live updates
        def update(frame):
            if not self._fwhm_history:
                return line,

            timestamps = [t for t, _ in self._fwhm_history]
            fwhms = [f for _, f in self._fwhm_history]
            t0 = timestamps[0]
            rel_times = [t - t0 for t in timestamps]

            line.set_data(rel_times, fwhms)
            ax.relim()
            ax.autoscale_view()

            # Update stats
            if fwhms:
                mean_fwhm = np.mean(fwhms)
                std_fwhm = np.std(fwhms)
                min_fwhm = np.min(fwhms)
                max_fwhm = np.max(fwhms)
                stats_text = f"Mean: {mean_fwhm:.3f}\"  Std: {std_fwhm:.3f}\"  Min: {min_fwhm:.3f}\"  Max: {max_fwhm:.3f}\""
                ax.set_title(f"Live FWHM Tracking\n{stats_text}", fontsize=10)

            return line,

        # Create animation that updates every 500ms
        # Store as instance variable to prevent garbage collection
        self._fwhm_animation = FuncAnimation(fig, update, interval=500, blit=False, cache_frame_data=False)
        self._fwhm_plot_fig = fig

        plt.tight_layout()
        plt.show(block=False)

        logger.info(f"FWHM plot opened with {len(self._fwhm_history)} data points")

    def _draw_fwhm_overlay(self, display_frame: np.ndarray) -> np.ndarray:
        """Draw FWHM tracking target overlay on display frame."""
        if self._fwhm_target is None:
            return display_frame

        # Convert to BGR for colored overlay if needed
        if len(display_frame.shape) == 2:
            display_frame = cv2.cvtColor(display_frame, cv2.COLOR_GRAY2BGR)

        # Convert image coordinates to display coordinates
        scale = self._display_scale_factor
        cx = int(self._fwhm_target[0] * scale)
        cy = int(self._fwhm_target[1] * scale)
        radius = int(self._fwhm_box_size * scale / 2)

        # Draw circle (magenta for FWHM to distinguish from photometry)
        cv2.circle(display_frame, (cx, cy), radius, (255, 0, 255), 2)

        # Draw crosshair (thicker)
        cv2.line(display_frame, (cx - 10, cy), (cx + 10, cy), (255, 0, 255), 2)
        cv2.line(display_frame, (cx, cy - 10), (cx, cy + 10), (255, 0, 255), 2)

        # Draw "F" label
        cv2.putText(display_frame, "F", (cx + radius + 10, cy + 10),
                   cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 0, 255), 3)

        # Draw FWHM value if available
        if self._fwhm_value is not None:
            text = f"{self._fwhm_value:.3f}\""
            text_x = cx + radius + 10
            text_y = cy + 50
            cv2.putText(display_frame, text, (text_x, text_y),
                       cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 255), 2)

        return display_frame

    def _display_loop(self):
        """Display loop (runs in MAIN THREAD via self.after to avoid Qt deadlocks)."""
        if not self._running:
            return

        # CRITICAL: Non-blocking lock prevents display update pileup
        # If previous update still processing, skip this frame (like v18 GUI)
        if not self._display_lock.acquire(blocking=False):
            # Schedule next update and return immediately
            if self._running:
                self._after_id = self.after(50, self._display_loop)  # Longer delay if skipping
            return

        try:
            # Create window on first call (in main thread - no Qt deadlock!)
            # Use WINDOW_NORMAL to allow user resize
            if not self._window_created:
                cv2.namedWindow(self._window_name, cv2.WINDOW_NORMAL)
                cv2.setMouseCallback(self._window_name, self._mouse_callback)
                self._window_created = True

            # Get latest frame from API - just grab ONE frame, don't drain
            frame = None
            try:
                frame = self.api.get_display_frame(timeout=0.001)
            except:
                pass

            # Display frame if we got one
            if frame is not None:
                self._last_frame = frame
                self._update_stats(frame)

                # Scale frame for display (contrast)
                display_frame = self._scale_frame(frame)

                # Scale up small frames to fill display better (like v18)
                # Track scale factor for mouse coordinate conversion
                height, width = display_frame.shape[:2]

                if width < self._min_display_size or height < self._min_display_size:
                    # Calculate scale factor to make smallest dimension at least MIN_DISPLAY_SIZE
                    # Use same factor for both dimensions to maintain aspect ratio
                    scale_factor = max(
                        self._min_display_size / width,
                        self._min_display_size / height
                    )
                    new_width = int(width * scale_factor)
                    new_height = int(height * scale_factor)
                    display_frame = cv2.resize(
                        display_frame,
                        (new_width, new_height),
                        interpolation=cv2.INTER_NEAREST
                    )
                    self._display_scale_factor = scale_factor
                else:
                    self._display_scale_factor = 1.0

                # Update FWHM measurement (throttled - every 5th frame)
                if self._fwhm_target is not None and self._frame_count % 5 == 0:
                    self._update_fwhm(frame)

                # Update photometry measurement
                self._update_photometry(frame)

                # Update guiding (throttled - every 10th frame, ~5 Hz)
                if self._guiding_enabled and self._frame_count % 10 == 0:
                    self._update_guiding()

                # Draw ROI rectangle if selecting
                display_frame = self._draw_roi_overlay(display_frame)

                # Draw FWHM tracking overlay
                display_frame = self._draw_fwhm_overlay(display_frame)

                # Draw aperture overlays for photometry
                display_frame = self._draw_aperture_overlay(display_frame)

                # Show frame (running in main thread - no deadlock!)
                cv2.imshow(self._window_name, display_frame)

                # Update cursor value from mouse position
                self._update_cursor_value(frame)

            # Handle OpenCV events
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:  # q or ESC
                self._stop_display()
                return

        except Exception as e:
            pass
        finally:
            self._display_lock.release()

        # Schedule next update (~50 Hz like v18 GUI)
        if self._running:
            self._after_id = self.after(50, self._display_loop)

    def _scale_frame(self, frame: np.ndarray) -> np.ndarray:
        """Scale frame for display."""
        if self.auto_scale_var.get():
            # Auto scale using min/max
            # CRITICAL OPTIMIZATION: Downsample for min/max calculation to reduce CPU
            # Computing min/max on full 2304x4096 array uses all CPU cores!
            # Sample every 8th pixel in each dimension (64x reduction)
            sample = frame[::8, ::8]
            vmin = int(np.min(sample))
            vmax = int(np.max(sample))
        else:
            try:
                vmin = float(self.scale_min_var.get())
                vmax = float(self.scale_max_var.get())
            except ValueError:
                vmin, vmax = 0, 65535

        # Clip and normalize
        if vmax <= vmin:
            vmax = vmin + 1
        scaled = np.clip(frame, vmin, vmax)
        scaled = ((scaled - vmin) / (vmax - vmin) * 255).astype(np.uint8)

        return scaled

    def _draw_roi_overlay(self, display_frame: np.ndarray) -> np.ndarray:
        """Draw ROI selection rectangle on display frame."""
        if self._roi_start_point is None or self._roi_end_point is None:
            return display_frame

        # Convert to BGR for colored rectangle
        if len(display_frame.shape) == 2:
            display_frame = cv2.cvtColor(display_frame, cv2.COLOR_GRAY2BGR)

        # Convert image coordinates to display coordinates using scale factor
        scale = self._display_scale_factor
        x1 = int(self._roi_start_point[0] * scale)
        y1 = int(self._roi_start_point[1] * scale)
        x2 = int(self._roi_end_point[0] * scale)
        y2 = int(self._roi_end_point[1] * scale)

        # Draw rectangle (green, 2px thick)
        cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

        # Draw size text (in image pixels, not display pixels)
        width = abs(self._roi_end_point[0] - self._roi_start_point[0])
        height = abs(self._roi_end_point[1] - self._roi_start_point[1])
        text = f"{width}x{height}"
        text_x = min(x1, x2) + 5
        text_y = min(y1, y2) - 10 if min(y1, y2) > 30 else max(y1, y2) + 20
        cv2.putText(display_frame, text, (text_x, text_y),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        return display_frame

    def _update_cursor_value(self, frame: np.ndarray):
        """Update cursor value from mouse position over display window."""
        try:
            if not self._running or frame is None or self._mouse_x is None or self._mouse_y is None:
                return

            height, width = frame.shape[:2]
            x, y = self._mouse_x, self._mouse_y

            if 0 <= x < width and 0 <= y < height:
                pixel_value = frame[y, x]
                # Update Tkinter var directly (we're already in main thread from display_loop)
                self.cursor_var.set(str(pixel_value))
            else:
                self.cursor_var.set("--")
        except Exception:
            pass

    def _update_stats(self, frame: np.ndarray):
        """Update statistics from frame."""
        self._frame_count += 1

        # Calculate FPS every second
        now = time.time()
        elapsed = now - self._fps_time
        if elapsed >= 1.0:
            fps = self._frame_count / elapsed
            self.fps_var.set(f"{fps:.1f}")
            self._frame_count = 0
            self._fps_time = now

        # Update stats (throttled and downsampled)
        if self._frame_count % 10 == 0:
            # Downsample for stats to reduce CPU usage
            sample = frame[::8, ::8]
            mean_val = np.mean(sample)
            max_val = np.max(sample)
            self.mean_var.set(f"{mean_val:.0f}")
            self.max_var.set(f"{max_val}")

    def update_from_state(self, state):
        """Update panel from system state."""
        pass  # Stats are updated in display loop

    # ===== Guiding Methods =====

    def _toggle_guiding(self):
        """Toggle guiding on/off."""
        if self._guiding_enabled_var.get():
            self._start_guiding()
        else:
            self._stop_guiding()

    def _start_guiding(self):
        """Start guiding - enter calibration mode."""
        if self._fwhm_target is None:
            logger.warning("Cannot start guiding: no FWHM target set")
            self._guiding_enabled_var.set(False)
            self._guiding_status_var.set("Set FWHM target first!")
            return

        if not self.api.state.telescope_connected:
            logger.warning("Cannot start guiding: telescope not connected")
            self._guiding_enabled_var.set(False)
            self._guiding_status_var.set("Telescope not connected!")
            return

        logger.info("Starting guiding calibration...")
        self._guiding_enabled = True
        self._guiding_calibrating = True
        self._guiding_reference = None
        self._position_history = []
        self._guiding_calibration_start = time.time()
        self._last_correction_time = 0
        self._guiding_status_var.set("Calibrating...")

    def _stop_guiding(self):
        """Stop guiding."""
        logger.info("Stopping guiding")
        self._guiding_enabled = False
        self._guiding_calibrating = False
        self._guiding_reference = None
        self._guiding_status_var.set("Not guiding")

    def _reset_guiding_reference(self):
        """Reset and recalibrate reference position."""
        if not self._guiding_enabled:
            return

        logger.info("Resetting guiding reference - recalibrating...")
        self._guiding_calibrating = True
        self._guiding_reference = None
        self._position_history = []
        self._guiding_calibration_start = time.time()
        self._guiding_status_var.set("Calibrating...")

    def _compute_average_position(self, window_seconds: float) -> Optional[Tuple[float, float]]:
        """
        Compute average centroid position over time window.

        Args:
            window_seconds: Time window in seconds

        Returns:
            (avg_x, avg_y) or None if insufficient data
        """
        if not self._position_history:
            return None

        now = time.time()
        cutoff = now - window_seconds

        # Filter to recent positions
        recent = [(t, x, y) for t, x, y in self._position_history if t >= cutoff]

        if len(recent) < 3:  # Need at least 3 samples
            return None

        avg_x = np.mean([x for _, x, _ in recent])
        avg_y = np.mean([y for _, _, y in recent])

        return (float(avg_x), float(avg_y))

    def _update_guiding(self):
        """
        Update guiding state and apply corrections if needed.

        Called from display loop when guiding is enabled.
        """
        if not self._guiding_enabled:
            return

        now = time.time()
        window = self._guiding_config.averaging_window_seconds

        if self._guiding_calibrating:
            # In calibration mode - collecting reference position
            elapsed = now - self._guiding_calibration_start
            remaining = window - elapsed

            if remaining > 0:
                self._guiding_status_var.set(f"Calibrating... {remaining:.1f}s")
            else:
                # Calibration complete - compute reference
                ref = self._compute_average_position(window)
                if ref is not None:
                    self._guiding_reference = ref
                    self._guiding_calibrating = False
                    self._last_correction_time = now
                    logger.info(f"Guiding reference set: ({ref[0]:.2f}, {ref[1]:.2f}) pixels")
                    self._guiding_status_var.set("Guiding active")
                else:
                    # Not enough data, extend calibration
                    self._guiding_status_var.set("Calibrating... (waiting for data)")
        else:
            # Active guiding - compare current position to reference
            current = self._compute_average_position(window)

            if current is None or self._guiding_reference is None:
                self._guiding_status_var.set("Guiding (waiting for data)")
                return

            # Compute drift in pixels
            drift_x = current[0] - self._guiding_reference[0]
            drift_y = current[1] - self._guiding_reference[1]

            # Convert to arcseconds
            drift_x_arcsec = drift_x * self._plate_scale
            drift_y_arcsec = drift_y * self._plate_scale
            drift_total_arcsec = np.sqrt(drift_x_arcsec**2 + drift_y_arcsec**2)

            # Update status
            self._guiding_status_var.set(
                f"Drift: {drift_total_arcsec:.2f}\" ({drift_x_arcsec:+.2f}, {drift_y_arcsec:+.2f})"
            )

            # Check if correction is needed
            threshold = self._guiding_config.correction_threshold_arcsec
            interval = self._guiding_config.correction_interval_seconds

            if drift_total_arcsec >= threshold and (now - self._last_correction_time) >= interval:
                self._apply_guiding_correction(drift_x_arcsec, drift_y_arcsec)

    def _apply_guiding_correction(self, drift_x_arcsec: float, drift_y_arcsec: float):
        """
        Apply a guiding correction to the telescope.

        Args:
            drift_x_arcsec: X drift in arcseconds (pixel coordinates)
            drift_y_arcsec: Y drift in arcseconds (pixel coordinates)
        """
        config = self._guiding_config

        # Apply coordinate sign mapping and gain
        ra_correction = drift_x_arcsec * config.x_to_ra_sign * config.guide_gain
        dec_correction = drift_y_arcsec * config.y_to_dec_sign * config.guide_gain

        # Apply max correction limit
        max_corr = config.max_correction_arcsec
        ra_correction = np.clip(ra_correction, -max_corr, max_corr)
        dec_correction = np.clip(dec_correction, -max_corr, max_corr)

        # Apply correction
        logger.info(f"Applying guiding correction: RA={ra_correction:+.3f}\", Dec={dec_correction:+.3f}\"")

        try:
            success = self.api.move_offset(ra_correction, dec_correction)
            if success:
                self._last_correction_time = time.time()
                # Clear position history after correction to let new reference settle
                self._position_history = []
            else:
                logger.warning("Guiding correction failed")
        except Exception as e:
            logger.error(f"Error applying guiding correction: {e}")

    def cleanup(self):
        """Cleanup resources."""
        # Stop the display loop first
        self._running = False

        # Cancel any pending after callbacks to prevent "invalid command" errors
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
                self._after_id = None
            except:
                pass

        # Close matplotlib figures to prevent orphaned windows
        try:
            import matplotlib.pyplot as plt
            if self._fwhm_plot_fig is not None:
                plt.close(self._fwhm_plot_fig)
                self._fwhm_plot_fig = None
                self._fwhm_animation = None
            if self._lightcurve_fig is not None:
                plt.close(self._lightcurve_fig)
                self._lightcurve_fig = None
                self._lightcurve_animation = None
        except:
            pass

        self._stop_display()
        # Clean up all OpenCV windows to avoid Qt warnings
        if CV2_AVAILABLE:
            try:
                cv2.destroyAllWindows()
                cv2.waitKey(1)  # Process any pending events
            except:
                pass

    # ========== Photometry Methods ==========

    def _toggle_photometry(self):
        """Toggle photometry mode."""
        self._photometry_enabled = self._phot_enabled_var.get()
        if self._photometry_enabled:
            logger.info("Photometry enabled - CTRL+click for target, ALT+click for comparison")
        else:
            logger.info("Photometry disabled")

    def _set_target_aperture(self, x: int, y: int):
        """Set target aperture position."""
        self._target_aperture = (x, y)
        self._target_status_var.set(f"T: ({x},{y})")
        self._photometry_data = []  # Clear data on new target
        logger.info(f"Target aperture set at ({x}, {y})")

    def _set_comparison_aperture(self, x: int, y: int):
        """Set comparison aperture position."""
        self._comparison_aperture = (x, y)
        self._comp_status_var.set(f"C: ({x},{y})")
        self._photometry_data = []  # Clear data on new comparison
        logger.info(f"Comparison aperture set at ({x}, {y})")

    def _clear_apertures(self):
        """Clear all aperture positions."""
        self._target_aperture = None
        self._comparison_aperture = None
        self._target_status_var.set("T: --")
        self._comp_status_var.set("C: --")
        self._photometry_data = []
        logger.info("Apertures cleared")

    def _compute_flux(self, frame: np.ndarray, cx: int, cy: int) -> Optional[float]:
        """
        Compute background-subtracted aperture flux.

        Args:
            frame: Image data
            cx, cy: Center coordinates

        Returns:
            Background-subtracted flux, or None if out of bounds
        """
        r_ap = self._aperture_radius.get()
        r_in = self._annulus_inner.get()
        r_out = self._annulus_outer.get()

        height, width = frame.shape[:2]

        # Check bounds
        if (cx - r_out < 0 or cx + r_out >= width or
            cy - r_out < 0 or cy + r_out >= height):
            return None

        # Extract subregion for efficiency
        x0 = max(0, cx - r_out - 1)
        x1 = min(width, cx + r_out + 2)
        y0 = max(0, cy - r_out - 1)
        y1 = min(height, cy + r_out + 2)

        subdata = frame[y0:y1, x0:x1]
        sub_cx = cx - x0
        sub_cy = cy - y0

        # Create coordinate grids for subregion
        y_grid, x_grid = np.ogrid[:subdata.shape[0], :subdata.shape[1]]
        dist_sq = (x_grid - sub_cx)**2 + (y_grid - sub_cy)**2

        # Aperture and annulus masks
        aperture_mask = dist_sq <= r_ap**2
        annulus_mask = (dist_sq >= r_in**2) & (dist_sq <= r_out**2)

        # Background from annulus
        annulus_pixels = subdata[annulus_mask]
        if len(annulus_pixels) == 0:
            return None
        background = np.median(annulus_pixels)

        # Source flux
        aperture_pixels = subdata[aperture_mask]
        n_pixels = len(aperture_pixels)
        flux = np.sum(aperture_pixels.astype(np.float64)) - (background * n_pixels)

        return float(flux)

    def _update_photometry(self, frame: np.ndarray):
        """Update photometry measurements."""
        if not self._photometry_enabled or self._target_aperture is None:
            return

        # Rate limit to ~20 Hz
        current_time = time.time()
        if current_time - self._last_photometry_time < 0.05:
            return
        self._last_photometry_time = current_time

        # Compute target flux
        target_flux = self._compute_flux(frame, self._target_aperture[0], self._target_aperture[1])

        # Compute comparison flux if set
        if self._comparison_aperture is not None:
            comp_flux = self._compute_flux(frame, self._comparison_aperture[0], self._comparison_aperture[1])
        else:
            comp_flux = None

        # Calculate relative flux
        if target_flux is not None and comp_flux is not None and comp_flux > 0:
            relative_flux = target_flux / comp_flux
        else:
            relative_flux = None

        # Store data point
        data_point = {
            'time': current_time,
            'target_flux': target_flux,
            'comp_flux': comp_flux,
            'relative_flux': relative_flux
        }
        self._photometry_data.append(data_point)

        # Trim buffer if needed
        if len(self._photometry_data) > self._photometry_data_max:
            self._photometry_data = self._photometry_data[-self._photometry_data_max:]

    def _draw_aperture_overlay(self, display_frame: np.ndarray) -> np.ndarray:
        """Draw aperture circles on display frame."""
        if not self._photometry_enabled:
            return display_frame

        if self._target_aperture is None and self._comparison_aperture is None:
            return display_frame

        # Convert to BGR for colored overlay if needed
        if len(display_frame.shape) == 2:
            display_frame = cv2.cvtColor(display_frame, cv2.COLOR_GRAY2BGR)

        scale = self._display_scale_factor
        r_ap = int(self._aperture_radius.get() * scale)
        r_in = int(self._annulus_inner.get() * scale)
        r_out = int(self._annulus_outer.get() * scale)

        # Draw target aperture (green)
        if self._target_aperture is not None:
            x, y = self._target_aperture
            dx, dy = int(x * scale), int(y * scale)
            cv2.circle(display_frame, (dx, dy), r_ap, (0, 255, 0), 2)  # Aperture
            cv2.circle(display_frame, (dx, dy), r_in, (0, 255, 0), 2)  # Inner annulus
            cv2.circle(display_frame, (dx, dy), r_out, (0, 255, 0), 2)  # Outer annulus
            cv2.putText(display_frame, "T", (dx + r_out + 10, dy + 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)

        # Draw comparison aperture (cyan)
        if self._comparison_aperture is not None:
            x, y = self._comparison_aperture
            dx, dy = int(x * scale), int(y * scale)
            cv2.circle(display_frame, (dx, dy), r_ap, (255, 255, 0), 2)  # Aperture
            cv2.circle(display_frame, (dx, dy), r_in, (255, 255, 0), 2)  # Inner annulus
            cv2.circle(display_frame, (dx, dy), r_out, (255, 255, 0), 2)  # Outer annulus
            cv2.putText(display_frame, "C", (dx + r_out + 10, dy + 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 0), 3)

        return display_frame

    def _show_lightcurve(self):
        """Show the lightcurve plot window."""
        if not self._photometry_data:
            logger.info("No photometry data to plot")
            return

        try:
            import matplotlib
            matplotlib.use('TkAgg')
            import matplotlib.pyplot as plt
            from matplotlib.animation import FuncAnimation
        except ImportError:
            logger.warning("matplotlib not available for lightcurve plotting")
            return

        # Create figure
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        fig.canvas.manager.set_window_title("Live Lightcurve")

        # Initialize lines
        line_target, = ax1.plot([], [], 'g-', linewidth=1, label='Target')
        line_comp, = ax1.plot([], [], 'c-', linewidth=1, label='Comparison')
        line_rel, = ax2.plot([], [], 'b-', linewidth=1, marker='.', markersize=2)

        ax1.set_ylabel("Raw Flux (ADU)")
        ax1.set_title("Aperture Photometry")
        ax1.legend(loc='upper right')
        ax1.grid(True, alpha=0.3)

        ax2.set_xlabel("Time (seconds)")
        ax2.set_ylabel("Relative Flux (T/C)")
        ax2.grid(True, alpha=0.3)

        def update(frame):
            if not self._photometry_data:
                return line_target, line_comp, line_rel

            # Extract data
            t0 = self._photometry_data[0]['time']
            times = [d['time'] - t0 for d in self._photometry_data]
            target_flux = [d['target_flux'] for d in self._photometry_data if d['target_flux'] is not None]
            comp_flux = [d['comp_flux'] for d in self._photometry_data if d['comp_flux'] is not None]
            rel_flux = [d['relative_flux'] for d in self._photometry_data if d['relative_flux'] is not None]

            # Filter times for each dataset
            times_target = [d['time'] - t0 for d in self._photometry_data if d['target_flux'] is not None]
            times_comp = [d['time'] - t0 for d in self._photometry_data if d['comp_flux'] is not None]
            times_rel = [d['time'] - t0 for d in self._photometry_data if d['relative_flux'] is not None]

            # Update lines
            if times_target and target_flux:
                line_target.set_data(times_target, target_flux)
            if times_comp and comp_flux:
                line_comp.set_data(times_comp, comp_flux)
            if times_rel and rel_flux:
                line_rel.set_data(times_rel, rel_flux)

            # Adjust axes
            ax1.relim()
            ax1.autoscale_view()
            ax2.relim()
            ax2.autoscale_view()

            # Update stats in title
            if rel_flux:
                mean_rel = np.mean(rel_flux)
                std_rel = np.std(rel_flux)
                ax2.set_title(f"Mean: {mean_rel:.4f}  Std: {std_rel:.4f} ({std_rel/mean_rel*100:.2f}%)")

            return line_target, line_comp, line_rel

        # Create animation
        self._lightcurve_animation = FuncAnimation(fig, update, interval=500, blit=False, cache_frame_data=False)
        self._lightcurve_fig = fig

        plt.tight_layout()
        plt.show(block=False)

        logger.info(f"Lightcurve plot opened with {len(self._photometry_data)} data points")
