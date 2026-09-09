"""Telescope window: connection, position/status readout, offset moves."""

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from ..theme import set_status_label
from ..workers import run_async

logger = logging.getLogger(__name__)


def _val(text="--") -> QLabel:
    lbl = QLabel(text)
    lbl.setMinimumWidth(120)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


class TelescopeSettingsWindow(QWidget):
    def __init__(self, api, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.api = api
        self.setWindowTitle("Telescope")
        self.resize(560, 520)
        self._busy = False
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        conn = QGroupBox("Connection")
        cl = QHBoxLayout(conn)
        self.connect_btn = QPushButton("Connect TCS")
        self.connect_btn.clicked.connect(self._on_connect)
        cl.addWidget(self.connect_btn)
        self.status_label = QLabel("Disconnected")
        set_status_label(self.status_label, "Disconnected", "muted")
        cl.addWidget(self.status_label)
        cl.addStretch()
        layout.addWidget(conn)

        pos = QGroupBox("Position")
        g = QGridLayout(pos)
        self.ra = _val(); self.dec = _val(); self.ha = _val(); self.lst = _val()
        self.airmass = _val(); self.utc = _val()
        for r, pairs in enumerate([(("RA:", self.ra), ("Dec:", self.dec)),
                                   (("HA:", self.ha), ("LST:", self.lst)),
                                   (("Airmass:", self.airmass), ("UTC:", self.utc))]):
            for c, (lbl, w) in enumerate(pairs):
                g.addWidget(QLabel(lbl), r, c * 2)
                g.addWidget(w, r, c * 2 + 1)
        layout.addWidget(pos)

        st = QGroupBox("Status")
        g = QGridLayout(st)
        self.focus = _val(); self.tube = _val(); self.off_ra = _val(); self.off_dec = _val()
        self.rate_ra = _val(); self.rate_dec = _val(); self.cass = _val(); self.telid = _val()
        for r, pairs in enumerate([(("Focus:", self.focus), ("Tube length:", self.tube)),
                                   (("Offset RA:", self.off_ra), ("Offset Dec:", self.off_dec)),
                                   (("Rate RA:", self.rate_ra), ("Rate Dec:", self.rate_dec)),
                                   (("Cass ring:", self.cass), ("Telescope ID:", self.telid))]):
            for c, (lbl, w) in enumerate(pairs):
                g.addWidget(QLabel(lbl), r, c * 2)
                g.addWidget(w, r, c * 2 + 1)
        layout.addWidget(st)

        mv = QGroupBox("Offset Move")
        ml = QVBoxLayout(mv)
        row = QHBoxLayout()
        row.addWidget(QLabel("RA:"))
        self.offset_ra = QDoubleSpinBox()
        self.offset_ra.setRange(-3600, 3600)
        self.offset_ra.setDecimals(2)
        self.offset_ra.setSuffix(" ″")
        row.addWidget(self.offset_ra)
        row.addWidget(QLabel("Dec:"))
        self.offset_dec = QDoubleSpinBox()
        self.offset_dec.setRange(-3600, 3600)
        self.offset_dec.setDecimals(2)
        self.offset_dec.setSuffix(" ″")
        row.addWidget(self.offset_dec)
        self.move_btn = QPushButton("Move Offset")
        self.move_btn.clicked.connect(self._on_move_offset)
        row.addWidget(self.move_btn)
        row.addStretch()
        ml.addLayout(row)

        pad = QGridLayout()
        pad.addWidget(QLabel("Nudge step:"), 0, 0)
        self.step = QDoubleSpinBox()
        self.step.setRange(0.01, 600)
        self.step.setValue(1.0)
        self.step.setDecimals(2)
        self.step.setSuffix(" ″")
        pad.addWidget(self.step, 0, 1)
        self.btn_n = QPushButton("N")
        self.btn_s = QPushButton("S")
        self.btn_e = QPushButton("E")
        self.btn_w = QPushButton("W")
        for b in (self.btn_n, self.btn_s, self.btn_e, self.btn_w):
            b.setFixedWidth(44)
        pad.addWidget(self.btn_n, 0, 4)
        pad.addWidget(self.btn_w, 1, 3)
        pad.addWidget(self.btn_e, 1, 5)
        pad.addWidget(self.btn_s, 2, 4)
        pad.setColumnStretch(2, 1)
        pad.setColumnStretch(6, 1)
        self.btn_n.clicked.connect(lambda: self._nudge(0, +1))
        self.btn_s.clicked.connect(lambda: self._nudge(0, -1))
        self.btn_e.clicked.connect(lambda: self._nudge(+1, 0))
        self.btn_w.clicked.connect(lambda: self._nudge(-1, 0))
        ml.addLayout(pad)
        self.move_status = QLabel("")
        self.move_status.setObjectName("hint")
        ml.addWidget(self.move_status)
        layout.addWidget(mv)

        layout.addStretch()
        btns = QHBoxLayout()
        btns.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        btns.addWidget(close)
        layout.addLayout(btns)

    # ---- actions ----------------------------------------------------------------

    def _on_connect(self):
        if self._busy:
            return
        self._busy = True
        self.connect_btn.setEnabled(False)
        if self.api.state.telescope_connected:
            set_status_label(self.status_label, "Disconnecting…", "warn")
            run_async(self.api.disconnect_telescope, on_finally=self._connect_finished, name="disconnect_tcs")
        else:
            set_status_label(self.status_label, "Connecting…", "warn")

            def done(ok):
                if not ok:
                    set_status_label(self.status_label, "Failed", "err")

            run_async(self.api.connect_telescope, on_done=done, on_finally=self._connect_finished, name="connect_tcs")

    def _connect_finished(self):
        self._busy = False
        self.connect_btn.setEnabled(True)

    def _do_move(self, ra: float, dec: float):
        if not self.api.state.telescope_connected:
            self.move_status.setText("Telescope not connected")
            return
        self.move_status.setText(f"Moving RA {ra:+.2f}″  Dec {dec:+.2f}″ …")
        for b in (self.move_btn, self.btn_n, self.btn_s, self.btn_e, self.btn_w):
            b.setEnabled(False)

        def done(ok):
            self.move_status.setText("Move sent" if ok else "TCS rejected offset move")

        def finished():
            for b in (self.move_btn, self.btn_n, self.btn_s, self.btn_e, self.btn_w):
                b.setEnabled(True)

        run_async(self.api.move_offset, ra, dec, on_done=done, on_finally=finished, name="move_offset")

    def _on_move_offset(self):
        self._do_move(self.offset_ra.value(), self.offset_dec.value())

    def _nudge(self, ra_sign: int, dec_sign: int):
        step = self.step.value()
        self._do_move(ra_sign * step, dec_sign * step)

    # ---- state -----------------------------------------------------------------

    def update_from_state(self, state):
        if not self._busy:
            if state.telescope_connected:
                self.connect_btn.setText("Disconnect")
                set_status_label(self.status_label, "Connected", "ok")
            else:
                self.connect_btn.setText("Connect TCS")
                set_status_label(self.status_label, "Disconnected", "muted")

        def f(v, fmt="{:.2f}", suffix=""):
            return (fmt.format(v) + suffix) if v is not None else "--"

        self.ra.setText(state.telescope_ra or "--")
        self.dec.setText(state.telescope_dec or "--")
        self.ha.setText(state.telescope_ha or "--")
        self.lst.setText(state.telescope_lst or "--")
        self.airmass.setText(f(state.telescope_airmass, "{:.3f}"))
        self.utc.setText(state.telescope_utc or "--")
        self.focus.setText(f(state.telescope_focus, "{:.2f}", " mm"))
        self.tube.setText(f(state.telescope_tube_length_mm, "{:.2f}", " mm"))
        self.off_ra.setText(f(state.telescope_offset_ra_arcsec, "{:+.2f}", " ″"))
        self.off_dec.setText(f(state.telescope_offset_dec_arcsec, "{:+.2f}", " ″"))
        self.rate_ra.setText(f(state.telescope_rate_ra_arcsec_hr, "{:+.2f}", " ″/hr"))
        self.rate_dec.setText(f(state.telescope_rate_dec_arcsec_hr, "{:+.2f}", " ″/hr"))
        self.cass.setText(f(state.telescope_cass_ring_angle, "{:.2f}", "°"))
        self.telid.setText(str(state.telescope_id) if state.telescope_id is not None else "--")
