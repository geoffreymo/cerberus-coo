# gui/panels/image_display.py
"""Image display panel for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
import numpy as np
from typing import TYPE_CHECKING, Optional
import threading
import time

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
        self._display_thread: Optional[threading.Thread] = None
        self._running = False
        self._last_frame: Optional[np.ndarray] = None

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
        """Start the display thread."""
        if not CV2_AVAILABLE:
            return

        self._running = True
        self._display_thread = threading.Thread(
            target=self._display_loop,
            name="DisplayThread",
            daemon=True
        )
        self._display_thread.start()
        self.display_btn.config(text="Close Display")

    def _stop_display(self):
        """Stop the display thread."""
        self._running = False
        if self._display_thread is not None:
            self._display_thread.join(timeout=1.0)
            self._display_thread = None

        if CV2_AVAILABLE:
            try:
                cv2.destroyWindow(self._window_name)
            except:
                pass

        self.display_btn.config(text="Open Display")

    def _display_loop(self):
        """Display loop (runs in separate thread)."""
        cv2.namedWindow(self._window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self._window_name, *self.display_size)

        while self._running:
            try:
                # Get frame from API
                frame = self.api.get_display_frame(timeout=0.05)

                if frame is not None:
                    self._last_frame = frame
                    self._update_stats(frame)

                    # Scale frame for display
                    display_frame = self._scale_frame(frame)

                    # Show frame
                    cv2.imshow(self._window_name, display_frame)

                # Handle OpenCV events
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q') or key == 27:  # q or ESC
                    self._running = False
                    break

            except Exception as e:
                time.sleep(0.01)

        try:
            cv2.destroyWindow(self._window_name)
        except:
            pass

        # Update button in main thread
        self.after(0, lambda: self.display_btn.config(text="Open Display"))

    def _scale_frame(self, frame: np.ndarray) -> np.ndarray:
        """Scale frame for display."""
        if self.auto_scale_var.get():
            # Auto scale based on percentiles
            p1, p99 = np.percentile(frame, [1, 99])
            vmin, vmax = p1, p99
        else:
            try:
                vmin = float(self.scale_min_var.get())
                vmax = float(self.scale_max_var.get())
            except ValueError:
                vmin, vmax = 0, 65535

        # Clip and normalize
        vmax = max(vmax, vmin + 1)
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

        # Update stats (throttled)
        if self._frame_count % 10 == 0:
            mean_val = np.mean(frame)
            max_val = np.max(frame)
            self.mean_var.set(f"{mean_val:.0f}")
            self.max_var.set(f"{max_val}")

    def update_from_state(self, state):
        """Update panel from system state."""
        pass  # Stats are updated in display loop

    def cleanup(self):
        """Cleanup resources."""
        self._stop_display()
