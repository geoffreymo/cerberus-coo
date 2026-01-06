# gui/panels/filter_panel.py
"""Filter wheel controls panel for Cerberus GUI."""

import threading
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...api import CerberusAPI


class FilterPanel(ttk.LabelFrame):
    """
    Panel for filter wheel controls.

    Includes filter selection and connection controls.
    """

    def __init__(self, parent, api: 'CerberusAPI'):
        super().__init__(parent, text="Filter Wheel", padding=5)
        self.api = api

        # Variables
        self.current_filter_var = tk.StringVar(value="--")
        self.selected_filter_var = tk.StringVar(value="")

        self._create_widgets()

    def _create_widgets(self):
        """Create panel widgets."""
        # Connection
        conn_frame = ttk.Frame(self)
        conn_frame.pack(fill=tk.X, pady=2)

        self.connect_btn = ttk.Button(
            conn_frame, text="Connect", command=self._on_connect
        )
        self.connect_btn.pack(side=tk.LEFT, padx=2)

        self.status_label = ttk.Label(conn_frame, text="Disconnected")
        self.status_label.pack(side=tk.LEFT, padx=10)

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # Current filter
        current_frame = ttk.Frame(self)
        current_frame.pack(fill=tk.X, pady=2)

        ttk.Label(current_frame, text="Current:").pack(side=tk.LEFT)
        ttk.Label(
            current_frame, textvariable=self.current_filter_var,
            width=15, font=('TkDefaultFont', 10, 'bold')
        ).pack(side=tk.LEFT, padx=5)

        # Filter selection
        select_frame = ttk.Frame(self)
        select_frame.pack(fill=tk.X, pady=2)

        ttk.Label(select_frame, text="Select:").pack(side=tk.LEFT)
        self.filter_combo = ttk.Combobox(
            select_frame,
            textvariable=self.selected_filter_var,
            width=15,
            state="readonly"
        )
        self.filter_combo.pack(side=tk.LEFT, padx=5)

        ttk.Button(
            select_frame, text="Go", command=self._on_filter_change, width=5
        ).pack(side=tk.LEFT, padx=2)

    def _on_connect(self):
        """Handle connect button click."""
        if self.api.state.filterwheel_connected:
            # Disconnect is fast, can do synchronously
            self.api.disconnect_filterwheel()
            self.connect_btn.config(text="Connect")
            self.status_label.config(text="Disconnected", foreground="black")
            self.filter_combo['values'] = []
        else:
            # Connection is slow, do in background thread
            import threading
            self.connect_btn.config(state=tk.DISABLED)
            self.status_label.config(text="Connecting...", foreground="orange")

            def connect_thread():
                success = self.api.connect_filterwheel()
                # Update GUI in main thread
                self.after(0, lambda: self._on_connect_complete(success))

            threading.Thread(target=connect_thread, daemon=True).start()

    def _on_connect_complete(self, success):
        """Called when filterwheel connection completes."""
        self.connect_btn.config(state=tk.NORMAL)

        if success:
            self.connect_btn.config(text="Disconnect")
            self.status_label.config(text="Connected", foreground="green")
            # Update filter list
            filters = self.api.get_available_filters()
            self.filter_combo['values'] = filters
            if filters:
                self.selected_filter_var.set(filters[0])
        else:
            self.status_label.config(text="Failed", foreground="red")

    def _on_filter_change(self):
        """Handle filter selection change."""
        if not self.api.state.filterwheel_connected:
            return

        selected = self.selected_filter_var.get()
        if selected:
            try:
                self.api.set_filter(selected)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to set filter: {e}")

    def update_from_state(self, state):
        """Update panel from system state."""
        try:
            if state.filterwheel_connected:
                self.connect_btn.config(text="Disconnect")
                self.status_label.config(text="Connected", foreground="green")
                if state.available_filters:
                    self.filter_combo['values'] = state.available_filters
            else:
                self.connect_btn.config(text="Connect")
                self.status_label.config(text="Disconnected", foreground="black")

            self.current_filter_var.set(state.current_filter or "--")
        except tk.TclError:
            pass  # Ignore 'popdown' errors when combobox dropdown is open
