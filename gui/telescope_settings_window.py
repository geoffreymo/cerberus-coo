# gui/telescope_settings_window.py
"""Telescope settings window for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..api import CerberusAPI

logger = logging.getLogger(__name__)


class TelescopeSettingsWindow(tk.Toplevel):
    """
    Window showing telescope controls and status.

    Includes connection, position display, and offset moves.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent)
        self.api = api
        self.title("Telescope Settings")
        self.geometry("500x500")
        self.minsize(450, 450)

        # Variables
        self.ra_var = tk.StringVar(value="--")
        self.dec_var = tk.StringVar(value="--")
        self.ha_var = tk.StringVar(value="--")
        self.lst_var = tk.StringVar(value="--")
        self.airmass_var = tk.StringVar(value="--")
        self.utc_var = tk.StringVar(value="--")

        # Offset move
        self.offset_ra_var = tk.StringVar(value="0")
        self.offset_dec_var = tk.StringVar(value="0")

        self._create_widgets()

        # Initial update
        self.update_from_state(api.state)

    def _create_widgets(self):
        """Create window widgets."""
        # Main container with padding
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Connection frame
        conn_frame = ttk.LabelFrame(main_frame, text="Connection", padding=5)
        conn_frame.pack(fill=tk.X, pady=(0, 10))

        conn_inner = ttk.Frame(conn_frame)
        conn_inner.pack(fill=tk.X, pady=2)

        self.connect_btn = ttk.Button(
            conn_inner, text="Connect TCS", command=self._on_connect
        )
        self.connect_btn.pack(side=tk.LEFT, padx=2)

        self.status_label = ttk.Label(conn_inner, text="Disconnected")
        self.status_label.pack(side=tk.LEFT, padx=10)

        # Position display frame
        pos_frame = ttk.LabelFrame(main_frame, text="Position", padding=5)
        pos_frame.pack(fill=tk.X, pady=(0, 10))

        # Position grid
        row = 0

        ttk.Label(pos_frame, text="RA:").grid(row=row, column=0, sticky=tk.W, pady=2, padx=5)
        ttk.Label(pos_frame, textvariable=self.ra_var, width=14).grid(row=row, column=1, pady=2, padx=5, sticky=tk.W)
        ttk.Label(pos_frame, text="Dec:").grid(row=row, column=2, sticky=tk.W, pady=2, padx=5)
        ttk.Label(pos_frame, textvariable=self.dec_var, width=14).grid(row=row, column=3, pady=2, padx=5, sticky=tk.W)
        row += 1

        ttk.Label(pos_frame, text="HA:").grid(row=row, column=0, sticky=tk.W, pady=2, padx=5)
        ttk.Label(pos_frame, textvariable=self.ha_var, width=14).grid(row=row, column=1, pady=2, padx=5, sticky=tk.W)
        ttk.Label(pos_frame, text="LST:").grid(row=row, column=2, sticky=tk.W, pady=2, padx=5)
        ttk.Label(pos_frame, textvariable=self.lst_var, width=14).grid(row=row, column=3, pady=2, padx=5, sticky=tk.W)
        row += 1

        ttk.Label(pos_frame, text="Airmass:").grid(row=row, column=0, sticky=tk.W, pady=2, padx=5)
        ttk.Label(pos_frame, textvariable=self.airmass_var, width=14).grid(row=row, column=1, pady=2, padx=5, sticky=tk.W)
        ttk.Label(pos_frame, text="UTC:").grid(row=row, column=2, sticky=tk.W, pady=2, padx=5)
        ttk.Label(pos_frame, textvariable=self.utc_var, width=14).grid(row=row, column=3, pady=2, padx=5, sticky=tk.W)

        # Offset moves frame
        move_frame = ttk.LabelFrame(main_frame, text="Offset Move", padding=5)
        move_frame.pack(fill=tk.X, pady=(0, 10))

        # RA offset
        ra_frame = ttk.Frame(move_frame)
        ra_frame.pack(fill=tk.X, pady=2)

        ttk.Label(ra_frame, text="RA:").pack(side=tk.LEFT, padx=5)
        ttk.Entry(ra_frame, textvariable=self.offset_ra_var, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Label(ra_frame, text="arcsec").pack(side=tk.LEFT)

        # Dec offset
        dec_frame = ttk.Frame(move_frame)
        dec_frame.pack(fill=tk.X, pady=2)

        ttk.Label(dec_frame, text="Dec:").pack(side=tk.LEFT, padx=5)
        ttk.Entry(dec_frame, textvariable=self.offset_dec_var, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Label(dec_frame, text="arcsec").pack(side=tk.LEFT)

        ttk.Button(
            move_frame, text="Move Offset", command=self._on_move_offset
        ).pack(pady=5)

        # Close button
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side=tk.RIGHT)

    def _on_connect(self):
        """Handle connect button click."""
        if self.api.state.telescope_connected:
            # Disconnect is fast, can do synchronously
            self.api.disconnect_telescope()
            self.connect_btn.config(text="Connect TCS")
            self.status_label.config(text="Disconnected", foreground="black")
        else:
            # Connection is slow, do in background thread
            self.connect_btn.config(state=tk.DISABLED)
            self.status_label.config(text="Connecting...", foreground="orange")

            def connect_thread():
                success = self.api.connect_telescope()
                # Update GUI in main thread
                self.after(0, lambda: self._on_connect_complete(success))

            threading.Thread(target=connect_thread, daemon=True).start()

    def _on_connect_complete(self, success):
        """Called when telescope connection completes."""
        self.connect_btn.config(state=tk.NORMAL)

        if success:
            self.connect_btn.config(text="Disconnect")
            self.status_label.config(text="Connected", foreground="green")
        else:
            self.status_label.config(text="Failed", foreground="red")

    def _on_move_offset(self):
        """Handle move offset button click."""
        if not self.api.state.telescope_connected:
            return

        try:
            ra = float(self.offset_ra_var.get())
            dec = float(self.offset_dec_var.get())
            self.api.move_offset(ra, dec)
        except ValueError:
            pass
        except Exception as e:
            logger.error(f"Failed to move offset: {e}")

    def update_from_state(self, state):
        """Update window from system state."""
        try:
            if state.telescope_connected:
                self.connect_btn.config(text="Disconnect")
                self.status_label.config(text="Connected", foreground="green")
            else:
                self.connect_btn.config(text="Connect TCS")
                self.status_label.config(text="Disconnected", foreground="black")

            self.ra_var.set(state.telescope_ra or "--")
            self.dec_var.set(state.telescope_dec or "--")
            self.ha_var.set(state.telescope_ha or "--")
            self.lst_var.set(state.telescope_lst or "--")
            self.airmass_var.set(f"{state.telescope_airmass:.3f}" if state.telescope_airmass else "--")
            self.utc_var.set(state.telescope_utc or "--")
        except tk.TclError:
            pass  # Ignore errors if window is closing
