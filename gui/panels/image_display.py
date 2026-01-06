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
        self.after(20, self._display_loop)

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
            # Start ROI selection if SHIFT is held
            if shift_held:
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
            # Right-click to cancel ROI or show menu
            if self._roi_drag_active:
                self._cancel_roi_selection()

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

    def _display_loop(self):
        """Display loop (runs in MAIN THREAD via self.after to avoid Qt deadlocks)."""
        if not self._running:
            return

        # CRITICAL: Non-blocking lock prevents display update pileup
        # If previous update still processing, skip this frame (like v18 GUI)
        if not self._display_lock.acquire(blocking=False):
            # Schedule next update and return immediately
            if self._running:
                self.after(50, self._display_loop)  # Longer delay if skipping
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

                # Draw ROI rectangle if selecting
                display_frame = self._draw_roi_overlay(display_frame)

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
            self.after(50, self._display_loop)

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
            if frame is None or self._mouse_x is None or self._mouse_y is None:
                return

            height, width = frame.shape[:2]
            x, y = self._mouse_x, self._mouse_y

            if 0 <= x < width and 0 <= y < height:
                pixel_value = frame[y, x]
                # Update Tkinter var from main thread
                self.after(0, lambda v=pixel_value: self.cursor_var.set(str(v)))
            else:
                self.after(0, lambda: self.cursor_var.set("--"))
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

    def cleanup(self):
        """Cleanup resources."""
        self._stop_display()
        # Clean up all OpenCV windows to avoid Qt warnings
        if CV2_AVAILABLE:
            try:
                cv2.destroyAllWindows()
                cv2.waitKey(1)  # Process any pending events
            except:
                pass
