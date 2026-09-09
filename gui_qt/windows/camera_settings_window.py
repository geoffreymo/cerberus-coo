"""Camera settings window: common settings as dropdowns + full parameter table."""

import logging
from typing import List, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..theme import set_status_label
from ..workers import run_async

logger = logging.getLogger(__name__)

# (label, property name, [(display, value)], state-param key, text→display matcher)
_SETTINGS = [
    ("Binning", "BINNING", [("1x1", 1.0), ("2x2", 2.0), ("4x4", 4.0)], "BINNING"),
    ("Readout Speed", "READOUT_SPEED", [("Ultra Quiet", 1.0), ("Standard", 2.0)], "READOUT SPEED"),
    ("Sensor Mode", "SENSOR_MODE", [("Standard", 1.0), ("Photon Number", 12.0)], "SENSOR MODE"),
    ("Trigger Source", "TRIGGER_SOURCE", [("Internal", 1.0), ("External", 2.0), ("Software", 3.0)], "TRIGGER SOURCE"),
    ("Trigger Mode", "TRIGGER_MODE", [("Normal", 1.0), ("Start", 6.0)], "TRIGGER MODE"),
    ("Defect Correction", "DEFECT_CORRECT_MODE", [("OFF", 1.0), ("ON", 2.0)], "DEFECT CORRECT MODE"),
    ("Hot Pixel Level", "HOT_PIXEL_CORRECT_LEVEL", [("STANDARD", 1.0), ("MINIMUM", 2.0), ("AGGRESSIVE", 3.0)], "HOT PIXEL CORRECT LEVEL"),
]


def _match_index(prop: str, raw, options) -> int:
    """Map a DCAM value/text to the dropdown index."""
    if raw is None:
        return -1
    text = str(raw).upper()
    if prop == "BINNING":
        for i, (disp, val) in enumerate(options):
            if text in (disp.upper(), str(val), str(int(val))):
                return i
        return -1
    if prop == "SENSOR_MODE":
        return 1 if "PHOTON" in text else 0
    if prop == "READOUT_SPEED":
        return 0 if "ULTRA" in text or text == "1.0" else 1
    if prop == "TRIGGER_SOURCE":
        return 1 if "EXTERNAL" in text else (2 if "SOFTWARE" in text else 0)
    if prop == "TRIGGER_MODE":
        return 1 if "START" in text else 0
    if prop == "DEFECT_CORRECT_MODE":
        return 1 if text in ("ON", "2.0") else 0
    if prop == "HOT_PIXEL_CORRECT_LEVEL":
        return 2 if "AGGRESSIVE" in text else (1 if "MINIMUM" in text else 0)
    return -1


class CameraSettingsWindow(QWidget):
    def __init__(self, api, cameras: List[Tuple[int, str]], parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.api = api
        self.cameras = cameras
        self.camera_index = cameras[0][0]
        self.setWindowTitle("Camera Settings")
        self.resize(620, 760)
        self._combos = {}
        self._param_rows = {}
        self._last_state = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Camera:</b>"))
        self.camera_combo = QComboBox()
        for _, cid in self.cameras:
            self.camera_combo.addItem(cid)
        self.camera_combo.activated.connect(self._on_camera_change)
        top.addWidget(self.camera_combo)
        self.status_label = QLabel("")
        top.addWidget(self.status_label)
        top.addStretch()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(lambda: run_async(self.api.update_status, name="update_status"))
        top.addWidget(refresh)
        layout.addLayout(top)

        group = QGroupBox("Settings")
        form = QFormLayout(group)
        for label, prop, options, _key in _SETTINGS:
            combo = QComboBox()
            for disp, val in options:
                combo.addItem(disp, val)
            combo.activated.connect(lambda idx, p=prop, c=combo: self._on_setting(p, c.itemData(idx)))
            self._combos[prop] = combo
            form.addRow(label + ":", combo)
        layout.addWidget(group)

        pgroup = QGroupBox("All Camera Parameters")
        pl = QVBoxLayout(pgroup)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter parameters…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        pl.addWidget(self.filter_edit)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        pl.addWidget(self.table)
        layout.addWidget(pgroup, stretch=1)

        btns = QHBoxLayout()
        btns.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        btns.addWidget(close)
        layout.addLayout(btns)

    def select_camera(self, camera_index: int):
        for i, (idx, _) in enumerate(self.cameras):
            if idx == camera_index:
                self.camera_index = idx
                self.camera_combo.setCurrentIndex(i)
        if self._last_state is not None:
            self.update_from_state(self._last_state)

    def _on_camera_change(self, index: int):
        self.camera_index = self.cameras[index][0]
        self._param_rows = {}
        self.table.setRowCount(0)
        if self._last_state is not None:
            self.update_from_state(self._last_state)

    def _on_setting(self, prop: str, value: float):
        cam = self.api.state.get_camera(self.camera_index)
        if not cam.connected:
            return
        logger.info(f"Camera {self.camera_index}: {prop} -> {value}")
        run_async(self.api.set_camera_property, prop, float(value), camera_index=self.camera_index,
                  name=f"set_{prop}",
                  on_done=lambda ok: None if ok else logger.error(f"Failed to set {prop}"))

    def _apply_filter(self, text: str):
        text = text.lower()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            self.table.setRowHidden(row, bool(text) and text not in item.text().lower())

    def update_from_state(self, state):
        self._last_state = state
        cam = state.get_camera(self.camera_index)
        if not cam.connected:
            set_status_label(self.status_label, "Disconnected", "muted")
            self.table.setRowCount(0)
            self._param_rows = {}
            return
        set_status_label(self.status_label, "Connected", "ok")
        params = cam.params or {}
        if not params:
            return

        for label, prop, options, key in _SETTINGS:
            idx = _match_index(prop, params.get(key), options)
            combo = self._combos[prop]
            if idx >= 0 and combo.currentIndex() != idx:
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)

        keys = sorted(params.keys())
        if list(self._param_rows.keys()) != keys:
            self.table.setRowCount(len(keys))
            self._param_rows = {}
            for row, key in enumerate(keys):
                self.table.setItem(row, 0, QTableWidgetItem(key))
                self.table.setItem(row, 1, QTableWidgetItem(""))
                self._param_rows[key] = row
            self._apply_filter(self.filter_edit.text())
        for key, row in self._param_rows.items():
            value = params[key]
            text = f"{value:.6g}" if isinstance(value, float) else str(value)
            item = self.table.item(row, 1)
            if item.text() != text:
                item.setText(text)
