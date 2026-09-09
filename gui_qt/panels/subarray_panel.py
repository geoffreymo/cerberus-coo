"""Subarray (ROI) panel."""

import logging
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

from ..workers import run_async

if TYPE_CHECKING:
    from ...api import CerberusAPI

logger = logging.getLogger(__name__)

SENSOR_W, SENSOR_H = 4096, 2304


class SubarrayPanel(QGroupBox):
    """
    ROI controls. Changing the subarray requires the camera to be stopped, so
    every change is executed as one background job: stop → apply → restart.
    """

    reset_triggered = pyqtSignal()
    roi_applied = pyqtSignal(int, int)   # hpos, vpos (for nested ROI selection)

    def __init__(self, api: 'CerberusAPI', camera_index: int, parent: QWidget = None):
        super().__init__("Subarray (ROI)", parent)
        self.api = api
        self.camera_index = camera_index
        self._busy = False
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        top = QHBoxLayout()
        self.enable_cb = QCheckBox("Enable Subarray")
        self.enable_cb.clicked.connect(self._on_enable_clicked)
        top.addWidget(self.enable_cb)
        top.addStretch()
        self.current_label = QLabel("")
        self.current_label.setObjectName("hint")
        top.addWidget(self.current_label)
        layout.addLayout(top)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)

        def spin(maximum, value):
            s = QSpinBox()
            s.setRange(0, maximum)
            s.setSingleStep(4)
            s.setValue(value)
            s.setMaximumWidth(90)
            s.setEnabled(False)
            return s

        self.hpos = spin(SENSOR_W - 4, 0)
        self.hsize = spin(SENSOR_W, SENSOR_W)
        self.vpos = spin(SENSOR_H - 4, 0)
        self.vsize = spin(SENSOR_H, SENSOR_H)
        self.hsize.setMinimum(4)
        self.vsize.setMinimum(4)
        grid.addWidget(QLabel("HPOS:"), 0, 0)
        grid.addWidget(self.hpos, 0, 1)
        grid.addWidget(QLabel("HSIZE:"), 0, 2)
        grid.addWidget(self.hsize, 0, 3)
        grid.addWidget(QLabel("VPOS:"), 1, 0)
        grid.addWidget(self.vpos, 1, 1)
        grid.addWidget(QLabel("VSIZE:"), 1, 2)
        grid.addWidget(self.vsize, 1, 3)
        grid.setColumnStretch(4, 1)
        layout.addLayout(grid)

        btns = QHBoxLayout()
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply)
        btns.addWidget(self.apply_btn)
        self.reset_btn = QPushButton("Reset to Full Frame")
        self.reset_btn.clicked.connect(self._on_reset)
        btns.addWidget(self.reset_btn)
        btns.addStretch()
        layout.addLayout(btns)

        hint = QLabel("Values rounded to a multiple of 4.  SHIFT+drag on the live view to select an ROI.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    # ---- helpers ---------------------------------------------------------------

    def _set_fields_enabled(self, enabled: bool):
        for w in (self.hpos, self.hsize, self.vpos, self.vsize, self.apply_btn):
            w.setEnabled(enabled)

    def _run_camera_job(self, apply_fn: Callable[[], None], label: str):
        """Stop streaming if needed, apply, restart streaming. Runs in the background."""
        if self._busy:
            logger.warning("Subarray change already in progress")
            return
        self._busy = True
        self.setEnabled(False)

        def job():
            cam = self.api.state.get_camera(self.camera_index)
            was_streaming = cam.streaming
            if was_streaming:
                logger.info(f"Camera {self.camera_index}: stopping streaming to {label}")
                self.api.stop_streaming(camera_index=self.camera_index)
            apply_fn()
            if was_streaming:
                self.api.start_streaming(camera_index=self.camera_index)

        def finished():
            self._busy = False
            self.setEnabled(True)

        run_async(job, on_finally=finished, name=f"subarray:{label}")

    # ---- actions ---------------------------------------------------------------

    def _on_enable_clicked(self, checked: bool):
        self._set_fields_enabled(checked)
        mode = 2.0 if checked else 1.0   # SUBARRAY_MODE: 1 = OFF, 2 = ON

        def apply():
            if not self.api.set_camera_property("SUBARRAY_MODE", mode, camera_index=self.camera_index):
                logger.error(f"Camera {self.camera_index}: failed to set SUBARRAY_MODE")
            else:
                logger.info(f"Camera {self.camera_index}: subarray mode {'ON' if checked else 'OFF'}")

        self._run_camera_job(apply, "change subarray mode")
        if not checked:
            self.roi_applied.emit(0, 0)

    def _values(self):
        vals = [(s.value() // 4) * 4 for s in (self.hpos, self.hsize, self.vpos, self.vsize)]
        for s, v in zip((self.hpos, self.hsize, self.vpos, self.vsize), vals):
            s.setValue(v)
        return vals

    def _on_apply(self):
        hpos, hsize, vpos, vsize = self._values()
        self._apply_values(hpos, vpos, hsize, vsize, set_mode=False)

    def _apply_values(self, hpos: int, vpos: int, hsize: int, vsize: int, set_mode: bool):
        idx = self.camera_index

        def apply():
            logger.info(f"Camera {idx}: applying subarray HPOS={hpos} VPOS={vpos} HSIZE={hsize} VSIZE={vsize}")
            if set_mode:
                self.api.set_camera_property("SUBARRAY_MODE", 2.0, camera_index=idx)
            # Shrink first so the position is always valid
            self.api.set_camera_property("SUBARRAY_HSIZE", float(hsize), camera_index=idx)
            self.api.set_camera_property("SUBARRAY_VSIZE", float(vsize), camera_index=idx)
            self.api.set_camera_property("SUBARRAY_HPOS", float(hpos), camera_index=idx)
            self.api.set_camera_property("SUBARRAY_VPOS", float(vpos), camera_index=idx)
            self.api.set_camera_property("SUBARRAY_HSIZE", float(hsize), camera_index=idx)
            self.api.set_camera_property("SUBARRAY_VSIZE", float(vsize), camera_index=idx)

        self._run_camera_job(apply, "apply subarray")
        self.roi_applied.emit(hpos, vpos)

    def _on_reset(self):
        self.enable_cb.setChecked(False)
        self._set_fields_enabled(False)
        self.hpos.setValue(0)
        self.vpos.setValue(0)
        self.hsize.setValue(SENSOR_W)
        self.vsize.setValue(SENSOR_H)

        def apply():
            idx = self.camera_index
            self.api.set_camera_property("SUBARRAY_MODE", 1.0, camera_index=idx)
            self.api.set_camera_property("SUBARRAY_HPOS", 0.0, camera_index=idx)
            self.api.set_camera_property("SUBARRAY_VPOS", 0.0, camera_index=idx)
            self.api.set_camera_property("SUBARRAY_HSIZE", float(SENSOR_W), camera_index=idx)
            self.api.set_camera_property("SUBARRAY_VSIZE", float(SENSOR_H), camera_index=idx)

        self._run_camera_job(apply, "reset subarray")
        self.reset_triggered.emit()
        self.roi_applied.emit(0, 0)

    def apply_roi(self, hpos: int, vpos: int, hsize: int, vsize: int):
        """Apply an ROI chosen by dragging on the live view."""
        logger.info(f"Camera {self.camera_index}: ROI from drag: {hsize}x{vsize} at ({hpos}, {vpos})")
        self.enable_cb.setChecked(True)
        self._set_fields_enabled(True)
        self.hpos.setValue(hpos)
        self.vpos.setValue(vpos)
        self.hsize.setValue(hsize)
        self.vsize.setValue(vsize)
        self._apply_values(hpos, vpos, hsize, vsize, set_mode=True)

    def get_current_offset(self):
        if self.enable_cb.isChecked():
            return self.hpos.value(), self.vpos.value()
        return 0, 0

    def update_from_state(self, state, cam_state=None):
        cam = cam_state or state.get_camera(self.camera_index)
        p = cam.params or {}
        w, h = p.get('IMAGE WIDTH'), p.get('IMAGE HEIGHT')
        mode = str(p.get('SUBARRAY MODE', '')).upper()
        if w and h:
            try:
                txt = f"Image: {int(float(w))} x {int(float(h))}"
                if mode == 'ON' or mode == '2.0':
                    txt += f" @ ({int(float(p.get('SUBARRAY HPOS', 0)))}, {int(float(p.get('SUBARRAY VPOS', 0)))})"
                self.current_label.setText(txt)
            except (TypeError, ValueError):
                pass
        elif not cam.connected:
            self.current_label.setText("")
