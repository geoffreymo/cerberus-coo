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

        # Scaling
        self.scale_min_var = tk.StringVar(value="0")
        self.scale_max_var = tk.StringVar(value="65535")
        self.auto_scale_var = tk.BooleanVar(value=True)

        # Statistics
        self.fps_var = tk.StringVar(value="0.0")
        self.mean_var = tk.StringVar(value="0")
        self.max_var = tk.StringVar(value="0")

        # FPS calculation
        self._frame_count = 0
        self._fps_time = time.time()

        self._create_widgets()

    def _create_widgets(self):
        """Create panel widgets."""
        # Info row
        info_frame = ttk.Frame(self)
        info_frame.pack(fill=tk.X, pady=2)

        ttk.Label(info_frame, text="FPS:").pack(side=tk.LEFT)
        ttk.Label(info_frame, textvariable=self.fps_var, width=6).pack(side=tk.LEFT)

        ttk.Label(info_frame, text="Mean:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(info_frame, textvariable=self.mean_var, width=8).pack(side=tk.LEFT)

        ttk.Label(info_frame, text="Max:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(info_frame, textvariable=self.max_var, width=8).pack(side=tk.LEFT)

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
