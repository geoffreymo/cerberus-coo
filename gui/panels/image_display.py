# gui/panels/image_display.py
"""Image display panel for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
import numpy as np
from typing import TYPE_CHECKING, Optional
import time
import threading

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
    Includes basic contrast controls.
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
        self._display_width = 0
        self._display_height = 0

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
            main_height = root.winfo_height()

            # Position OpenCV window to the right of main window
            display_x = main_x + main_width + 10
            display_y = main_y

            # Calculate display width to maintain aspect ratio with main window height
            # Camera sensor is 4096 x 2304 (16:9 aspect ratio)
            display_height = main_height
            display_width = int(display_height * 4096 / 2304)  # 16:9 aspect for camera

            # Store display size for cursor coordinate conversion
            self._display_width = display_width
            self._display_height = display_height

            # Move and resize the OpenCV window
            cv2.moveWindow(self._window_name, display_x, display_y)
            cv2.resizeWindow(self._window_name, display_width, display_height)

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
        """OpenCV mouse callback for tracking cursor position."""
        if event == cv2.EVENT_MOUSEMOVE:
            self._mouse_x = x
            self._mouse_y = y
            # Update cursor value immediately on mouse move
            if self._last_frame is not None:
                self._update_cursor_value(self._last_frame)

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
            if not self._window_created:
                cv2.namedWindow(self._window_name, cv2.WINDOW_NORMAL)
                cv2.resizeWindow(self._window_name, *self.display_size)
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

                # Scale frame for display
                display_frame = self._scale_frame(frame)

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
