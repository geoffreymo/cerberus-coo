# gui/panels/telescope_panel.py
"""Telescope controls panel for Cerberus GUI."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...api import CerberusAPI


class TelescopePanel(ttk.LabelFrame):
    """
    Panel for telescope controls.

    Includes focus control, position display, and offset moves.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent, text="Telescope", padding=5)
        self.api = api

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

    def _create_widgets(self):
        """Create panel widgets."""
        # Connection
        conn_frame = ttk.Frame(self)
        conn_frame.pack(fill=tk.X, pady=2)

        self.connect_btn = ttk.Button(
            conn_frame, text="Connect TCS", command=self._on_connect
        )
        self.connect_btn.pack(side=tk.LEFT, padx=2)

        self.status_label = ttk.Label(conn_frame, text="Disconnected")
        self.status_label.pack(side=tk.LEFT, padx=10)

        # Position display - Row 1: RA and Dec
        pos_frame1 = ttk.Frame(self)
        pos_frame1.pack(fill=tk.X, pady=2)

        ttk.Label(pos_frame1, text="RA:").pack(side=tk.LEFT)
        ttk.Label(pos_frame1, textvariable=self.ra_var, width=12).pack(side=tk.LEFT)

        ttk.Label(pos_frame1, text="Dec:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(pos_frame1, textvariable=self.dec_var, width=12).pack(side=tk.LEFT)

        # Position display - Row 2: HA and LST
        pos_frame2 = ttk.Frame(self)
        pos_frame2.pack(fill=tk.X, pady=2)

        ttk.Label(pos_frame2, text="HA:").pack(side=tk.LEFT)
        ttk.Label(pos_frame2, textvariable=self.ha_var, width=12).pack(side=tk.LEFT)

        ttk.Label(pos_frame2, text="LST:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(pos_frame2, textvariable=self.lst_var, width=12).pack(side=tk.LEFT)

        # Position display - Row 3: Airmass and UTC
        pos_frame3 = ttk.Frame(self)
        pos_frame3.pack(fill=tk.X, pady=2)

        ttk.Label(pos_frame3, text="Airmass:").pack(side=tk.LEFT)
        ttk.Label(pos_frame3, textvariable=self.airmass_var, width=8).pack(side=tk.LEFT)

        ttk.Label(pos_frame3, text="UTC:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(pos_frame3, textvariable=self.utc_var, width=14).pack(side=tk.LEFT)

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # Offset moves
        move_frame = ttk.LabelFrame(self, text="Offset Move", padding=3)
        move_frame.pack(fill=tk.X, pady=2)

        # RA offset
        ra_frame = ttk.Frame(move_frame)
        ra_frame.pack(fill=tk.X, pady=2)

        ttk.Label(ra_frame, text="RA:").pack(side=tk.LEFT)
        ttk.Entry(ra_frame, textvariable=self.offset_ra_var, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(ra_frame, text="arcsec").pack(side=tk.LEFT)

        # Dec offset
        dec_frame = ttk.Frame(move_frame)
        dec_frame.pack(fill=tk.X, pady=2)

        ttk.Label(dec_frame, text="Dec:").pack(side=tk.LEFT)
        ttk.Entry(dec_frame, textvariable=self.offset_dec_var, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(dec_frame, text="arcsec").pack(side=tk.LEFT)

        ttk.Button(
            move_frame, text="Move Offset", command=self._on_move_offset
        ).pack(pady=2)

    def _on_connect(self):
        """Handle connect button click."""
        if self.api.state.telescope_connected:
            # Disconnect is fast, can do synchronously
            self.api.disconnect_telescope()
            self.connect_btn.config(text="Connect TCS")
            self.status_label.config(text="Disconnected", foreground="black")
        else:
            # Connection is slow, do in background thread
            import threading
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
            import logging
            logging.getLogger(__name__).error(f"Failed to move offset: {e}")

    def update_from_state(self, state):
        """Update panel from system state."""
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
