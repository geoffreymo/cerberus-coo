"""
Live view: a per-camera image window (zoom/pan, overlays, FWHM tracking,
guiding, photometry) plus a compact summary panel for the main window tab.
"""

import logging
import time
from typing import Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox, QToolButton,
                             QVBoxLayout, QWidget)

from .. import analysis
from ..display_worker import DisplayWorker, FrameResult
from ..theme import set_status_label
from ..widgets.image_view import ImageView
from ..widgets.plot_window import LightcurvePlotWindow, TimeSeriesPlotWindow

logger = logging.getLogger(__name__)


def _is_dark() -> bool:
    app = QApplication.instance()
    return bool(app.property("cerberus_dark")) if app else True


class LiveViewWindow(QWidget):
    roi_selected = pyqtSignal(int, int, int, int)   # hpos, vpos, hsize, vsize (sensor coords)
    summary_changed = pyqtSignal(dict)
    visibility_changed = pyqtSignal(bool)

    def __init__(self, api, camera_index: int, camera_id: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.api = api
        self.camera_index = camera_index
        self.camera_id = camera_id
        self.setWindowTitle(f"Live View - {camera_id}")
        self.resize(1280, 820)

        self.worker = DisplayWorker(api, camera_index)
        self.worker.frame_ready.connect(self._on_frame)
        self.worker.guiding_changed.connect(self._set_guiding_status)
        self.worker.fwhm_target_moved.connect(self._on_target_moved)

        self._subarray_offset = (0, 0)
        self._last_result: Optional[FrameResult] = None
        self._last_summary_time = 0.0
        self._fwhm_plot = None
        self._lc_plot = None
        self._target_aperture: Optional[Tuple[int, int]] = None
        self._comparison_aperture: Optional[Tuple[int, int]] = None

        self._build()
        self._push_aperture_settings()

    # ---- UI ----------------------------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # Image view is created first: toolbar toggles reference it
        self.view = ImageView()

        # Toolbar
        tb = QHBoxLayout()

        def tool(text, slot, tip="", checkable=False):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            if checkable:
                b.toggled.connect(slot)
            else:
                b.clicked.connect(slot)
            tb.addWidget(b)
            return b

        tool("Fit", lambda: self.view.fit_to_window(), "Fit image to window (F)")
        tool("1:1", lambda: self.view.zoom_1to1(), "Actual pixels (1)")
        tool("+", lambda: self.view.zoom_by(1.25), "Zoom in (+)")
        tool("−", lambda: self.view.zoom_by(0.8), "Zoom out (−)")
        self.flip_btn = tool("Flip E/W", self._on_flip, "Mirror horizontally (north up, east left)", checkable=True)
        self.flip_btn.setChecked(True)
        self.cross_btn = tool("Crosshair", lambda on: self.view.set_crosshair(on), "Centre crosshair", checkable=True)
        tb.addSpacing(12)
        tb.addWidget(QLabel("Click:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(ImageView.CLICK_MODES)
        self.mode_combo.setToolTip("Action for a plain left click/drag. Modifiers always work:\n"
                                   "SHIFT+drag = ROI, right-click = FWHM target,\nCTRL+click = target aperture, ALT+click = comparison")
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        tb.addWidget(self.mode_combo)
        tb.addSpacing(12)
        self.auto_cb = QCheckBox("Auto scale")
        self.auto_cb.toggled.connect(self._on_scale_changed)
        tb.addWidget(self.auto_cb)
        tb.addWidget(QLabel("Min:"))
        self.min_spin = QDoubleSpinBox()
        self.min_spin.setRange(0, 65535)
        self.min_spin.setDecimals(0)
        self.min_spin.setValue(200)
        self.min_spin.setSingleStep(10)
        self.min_spin.valueChanged.connect(self._on_scale_changed)
        tb.addWidget(self.min_spin)
        tb.addWidget(QLabel("Max:"))
        self.max_spin = QDoubleSpinBox()
        self.max_spin.setRange(1, 65535)
        self.max_spin.setDecimals(0)
        self.max_spin.setValue(300)
        self.max_spin.setSingleStep(10)
        self.max_spin.valueChanged.connect(self._on_scale_changed)
        tb.addWidget(self.max_spin)
        tb.addStretch()
        tool("Save PNG…", self._save_png, "Save the displayed image as PNG")
        outer.addLayout(tb)

        # Body: image + side panel
        body = QHBoxLayout()
        body.setSpacing(6)
        self.view.mouse_moved.connect(self._on_mouse_moved)
        self.view.mouse_left.connect(lambda: self.cursor_label.setText("--"))
        self.view.roi_dragged.connect(self._on_roi_dragged)
        self.view.fwhm_target_requested.connect(self._set_fwhm_target)
        self.view.target_aperture_requested.connect(self._set_target_aperture)
        self.view.comparison_aperture_requested.connect(self._set_comparison_aperture)
        self.view.zoom_changed.connect(lambda z: self.zoom_label.setText(f"{z * 100:.0f}%"))
        body.addWidget(self.view, stretch=1)

        side = QWidget()
        side.setFixedWidth(300)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)

        g = QGroupBox("Frame")
        f = QFormLayout(g)
        self.fps_label = QLabel("0.0")
        self.mean_label = QLabel("0")
        self.max_label = QLabel("0")
        self.size_label = QLabel("--")
        self.cursor_label = QLabel("--")
        self.scale_label = QLabel("--")
        self.zoom_label = QLabel("--")
        f.addRow("FPS:", self.fps_label)
        f.addRow("Mean:", self.mean_label)
        f.addRow("Max:", self.max_label)
        f.addRow("Size:", self.size_label)
        f.addRow("Cursor:", self.cursor_label)
        f.addRow("Scale:", self.scale_label)
        f.addRow("Zoom:", self.zoom_label)
        sl.addWidget(g)

        g = QGroupBox("FWHM")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        self.fwhm_label = QLabel("--")
        self.fwhm_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        row.addWidget(self.fwhm_label, stretch=1)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_fwhm_target)
        row.addWidget(clear)
        plot = QPushButton("Plot")
        plot.clicked.connect(self._show_fwhm_plot)
        row.addWidget(plot)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Box:"))
        self.box_spin = QDoubleSpinBox()
        self.box_spin.setRange(1.0, 10.0)
        self.box_spin.setSingleStep(0.5)
        self.box_spin.setDecimals(1)
        self.box_spin.setSuffix(" ″")
        self.box_spin.setValue(self.worker.get_settings().fwhm_box_arcsec)
        self.box_spin.valueChanged.connect(self._on_box_changed)
        row.addWidget(self.box_spin)
        self.box_px_label = QLabel("")
        self.box_px_label.setObjectName("hint")
        row.addWidget(self.box_px_label)
        row.addStretch()
        v.addLayout(row)
        hint = QLabel("Right-click a star to track it")
        hint.setObjectName("hint")
        v.addWidget(hint)
        sl.addWidget(g)

        g = QGroupBox("Guiding")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        self.guide_cb = QCheckBox("Enable")
        self.guide_cb.clicked.connect(self._on_guiding_clicked)
        row.addWidget(self.guide_cb)
        reset = QPushButton("Reset reference")
        reset.clicked.connect(self.worker.reset_guiding)
        row.addWidget(reset)
        row.addStretch()
        v.addLayout(row)
        self.guiding_label = QLabel("Not guiding")
        self.guiding_label.setWordWrap(True)
        v.addWidget(self.guiding_label)
        sl.addWidget(g)

        g = QGroupBox("Photometry")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        self.phot_cb = QCheckBox("Enable")
        self.phot_cb.toggled.connect(self._on_phot_toggled)
        row.addWidget(self.phot_cb)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_apertures)
        row.addWidget(clear)
        plot = QPushButton("Plot")
        plot.clicked.connect(self._show_lightcurve)
        row.addWidget(plot)
        row.addStretch()
        v.addLayout(row)
        self.t_label = QLabel("T: --")
        self.c_label = QLabel("C: --")
        self.rel_label = QLabel("T/C: --")
        v.addWidget(self.t_label)
        v.addWidget(self.c_label)
        v.addWidget(self.rel_label)
        f = QFormLayout()
        self.ap_spin = QSpinBox(); self.ap_spin.setRange(2, 200); self.ap_spin.setValue(20)
        self.in_spin = QSpinBox(); self.in_spin.setRange(3, 300); self.in_spin.setValue(30)
        self.out_spin = QSpinBox(); self.out_spin.setRange(4, 400); self.out_spin.setValue(45)
        for s in (self.ap_spin, self.in_spin, self.out_spin):
            s.setSuffix(" px")
            s.valueChanged.connect(self._push_aperture_settings)
        f.addRow("Aperture R:", self.ap_spin)
        f.addRow("Annulus in:", self.in_spin)
        f.addRow("Annulus out:", self.out_spin)
        v.addLayout(f)
        hint = QLabel("CTRL+click: target   ALT+click: comparison")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        sl.addWidget(g)

        g = QGroupBox("ROI")
        v = QVBoxLayout(g)
        self.roi_label = QLabel("SHIFT+drag on the image to select a subarray")
        self.roi_label.setObjectName("hint")
        self.roi_label.setWordWrap(True)
        v.addWidget(self.roi_label)
        sl.addWidget(g)
        sl.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        scroll.setFixedWidth(320)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body.addWidget(scroll)
        outer.addLayout(body, stretch=1)

        self.status_label = QLabel("Waiting for frames…")
        self.status_label.setObjectName("hint")
        outer.addWidget(self.status_label)

        # Keyboard shortcuts
        for key, slot in (("F", self.view.fit_to_window), ("1", self.view.zoom_1to1),
                          ("+", lambda: self.view.zoom_by(1.25)), ("-", lambda: self.view.zoom_by(0.8)),
                          ("Escape", self.close)):
            act = QAction(self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(slot)
            self.addAction(act)
        self._on_box_changed(self.box_spin.value())

    # ---- worker lifecycle ---------------------------------------------------------

    def start(self):
        if not self.worker.isRunning():
            self.worker.start()
        self.worker.pause(False)

    def showEvent(self, event):
        super().showEvent(event)
        self.start()
        self.visibility_changed.emit(True)

    def closeEvent(self, event):
        # Hide rather than destroy: state (targets, histories) survives re-opening
        event.ignore()
        self.hide()
        self.worker.pause(True)
        self.visibility_changed.emit(False)

    def shutdown(self):
        self.worker.stop()
        for w in (self._fwhm_plot, self._lc_plot):
            if w is not None:
                w.close()
        self.hide()

    # ---- frames ----------------------------------------------------------------------

    def _on_frame(self, result: FrameResult):
        try:
            self._last_result = result
            self.view.set_image8(result.image8)
            self.view.set_fwhm_overlay(result.fwhm_target, result.fwhm_box_px, result.fwhm_arcsec, result.fwhm_ok)
            self.fps_label.setText(f"{result.fps:.1f}")
            self.mean_label.setText(f"{result.mean:.0f}")
            self.max_label.setText(f"{result.max:.0f}")
            h, w = result.shape
            self.size_label.setText(f"{w} x {h}")
            self.scale_label.setText(f"{result.vmin:.0f} – {result.vmax:.0f}")
            if result.fwhm_target is not None:
                if result.fwhm_arcsec is not None:
                    self.fwhm_label.setText(f'{result.fwhm_arcsec:.3f}"')
                elif not result.fwhm_ok:
                    self.fwhm_label.setText("fit err")
            self._set_guiding_status(result.guiding_status)
            if self.phot_cb.isChecked():
                self.t_label.setText(f"T: {result.target_flux:.0f}" if result.target_flux is not None else "T: --")
                self.c_label.setText(f"C: {result.comp_flux:.0f}" if result.comp_flux is not None else "C: --")
                self.rel_label.setText(f"T/C: {result.relative_flux:.4f}" if result.relative_flux is not None else "T/C: --")
            self.status_label.setText(f"Frame {result.frame_index}")
            now = time.time()
            if now - self._last_summary_time > 0.5:
                self._last_summary_time = now
                self.summary_changed.emit({
                    'fps': result.fps, 'mean': result.mean, 'max': result.max,
                    'fwhm': result.fwhm_arcsec if result.fwhm_target is not None else None,
                    'fwhm_ok': result.fwhm_ok, 'guiding': result.guiding_status,
                })
        finally:
            self.worker.frame_consumed()

    def _on_mouse_moved(self, x: int, y: int):
        frame = self.worker.last_frame
        if frame is None:
            self.cursor_label.setText(f"({x}, {y})")
            return
        h, w = frame.shape[:2]
        if 0 <= x < w and 0 <= y < h:
            self.cursor_label.setText(f"({x}, {y}) = {int(frame[y, x])}")
        else:
            self.cursor_label.setText("--")

    # ---- toolbar -----------------------------------------------------------------------

    def _on_flip(self, on: bool):
        self.view.set_flipped(on)

    def _on_mode_changed(self, text: str):
        self.view.click_mode = text

    def _on_scale_changed(self, *_):
        auto = self.auto_cb.isChecked()
        self.min_spin.setEnabled(not auto)
        self.max_spin.setEnabled(not auto)
        self.worker.update_settings(auto_scale=auto, vmin=self.min_spin.value(), vmax=self.max_spin.value())

    def _save_png(self):
        if self._last_result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save PNG", f"{self.camera_id}_live.png", "PNG (*.png)")
        if path:
            self.view.grab().save(path)
            logger.info(f"Saved {path}")

    # ---- ROI --------------------------------------------------------------------------

    def set_subarray_offset(self, hpos: int, vpos: int):
        self._subarray_offset = (hpos, vpos)

    def _on_roi_dragged(self, x1, y1, x2, y2):
        roi = analysis.roi_from_drag((x1, y1), (x2, y2), self._subarray_offset)
        if roi is None:
            self.roi_label.setText("ROI too small (minimum 16 x 16)")
            return
        hpos, vpos, hsize, vsize = roi
        self.roi_label.setText(f"Selected {hsize} x {vsize} at ({hpos}, {vpos})")
        logger.info(f"[{self.camera_id}] ROI selected: HPOS={hpos} VPOS={vpos} HSIZE={hsize} VSIZE={vsize}")
        # Coordinates change with the subarray: drop targets tied to the old frame
        self._clear_fwhm_target()
        self._clear_apertures()
        self.roi_selected.emit(hpos, vpos, hsize, vsize)

    # ---- FWHM / guiding ----------------------------------------------------------------

    def _on_box_changed(self, value: float):
        self.worker.update_settings(fwhm_box_arcsec=value)
        self.box_px_label.setText(f"= {self.worker.fwhm_box_pixels()} px")

    def _set_fwhm_target(self, x: int, y: int):
        frame = self.worker.last_frame
        if frame is None:
            self.status_label.setText("No frame yet")
            return
        h, w = frame.shape[:2]
        half = self.worker.fwhm_box_pixels() // 2
        if x < half or x >= w - half or y < half or y >= h - half:
            self.status_label.setText("FWHM target too close to the edge for the current box size")
            return
        self.worker.set_fwhm_target(x, y)
        self.view.set_fwhm_overlay((x, y), self.worker.fwhm_box_pixels(), None, True)
        self.fwhm_label.setText("…")
        logger.info(f"[{self.camera_id}] FWHM target set at ({x}, {y})")

    def _on_target_moved(self, x: int, y: int):
        pass  # overlay is refreshed with every frame result

    def _clear_fwhm_target(self):
        if self.guide_cb.isChecked():
            self.stop_guiding()
        self.worker.set_fwhm_target(None)
        self.view.set_fwhm_overlay(None, 0, None)
        self.fwhm_label.setText("--")

    def _show_fwhm_plot(self):
        if self._fwhm_plot is None:
            self._fwhm_plot = TimeSeriesPlotWindow(f"FWHM History - {self.camera_id}", "FWHM (arcsec)",
                                                  self.worker.get_fwhm_history, dark=_is_dark())
        self._fwhm_plot.show()
        self._fwhm_plot.raise_()

    def _on_guiding_clicked(self, checked: bool):
        if checked:
            ok, msg = self.worker.start_guiding()
            if not ok:
                self.guide_cb.setChecked(False)
                set_status_label(self.guiding_label, msg, "err")
        else:
            self.worker.stop_guiding()

    def stop_guiding(self):
        if self.guide_cb.isChecked():
            self.guide_cb.setChecked(False)
        self.worker.stop_guiding()

    def _set_guiding_status(self, text: str):
        kind = "ok" if "active" in text or "Drift" in text else ("warn" if "Calibrat" in text else "muted")
        set_status_label(self.guiding_label, text, kind)

    # ---- photometry --------------------------------------------------------------------

    def _push_aperture_settings(self):
        r_ap, r_in, r_out = self.ap_spin.value(), self.in_spin.value(), self.out_spin.value()
        self.worker.update_settings(aperture_radius=r_ap, annulus_inner=r_in, annulus_outer=r_out,
                                    target_aperture=self._target_aperture,
                                    comparison_aperture=self._comparison_aperture,
                                    photometry_enabled=self.phot_cb.isChecked())
        self.view.set_apertures(self._target_aperture, self._comparison_aperture, (r_ap, r_in, r_out),
                                self.phot_cb.isChecked())

    def _on_phot_toggled(self, on: bool):
        self._push_aperture_settings()
        logger.info(f"[{self.camera_id}] Photometry {'enabled' if on else 'disabled'}")

    def _set_target_aperture(self, x: int, y: int):
        if not self.phot_cb.isChecked():
            self.phot_cb.setChecked(True)
        self._target_aperture = (x, y)
        self.worker.clear_photometry()
        self.t_label.setText(f"T: ({x}, {y})")
        self._push_aperture_settings()
        logger.info(f"[{self.camera_id}] Target aperture set at ({x}, {y})")

    def _set_comparison_aperture(self, x: int, y: int):
        if not self.phot_cb.isChecked():
            self.phot_cb.setChecked(True)
        self._comparison_aperture = (x, y)
        self.worker.clear_photometry()
        self.c_label.setText(f"C: ({x}, {y})")
        self._push_aperture_settings()
        logger.info(f"[{self.camera_id}] Comparison aperture set at ({x}, {y})")

    def _clear_apertures(self):
        self._target_aperture = None
        self._comparison_aperture = None
        self.worker.clear_photometry()
        self.t_label.setText("T: --")
        self.c_label.setText("C: --")
        self.rel_label.setText("T/C: --")
        self._push_aperture_settings()

    def _show_lightcurve(self):
        if self._lc_plot is None:
            self._lc_plot = LightcurvePlotWindow(self.worker.get_photometry_data, dark=_is_dark())
            self._lc_plot.setWindowTitle(f"Live Lightcurve - {self.camera_id}")
        self._lc_plot.show()
        self._lc_plot.raise_()


class LiveViewPanel(QGroupBox):
    """Compact 'Live View' group for the camera tab; owns the LiveViewWindow."""

    roi_selected = pyqtSignal(int, int, int, int)

    def __init__(self, api, camera_index: int, camera_id: str, parent=None):
        super().__init__("Live View", parent)
        self.api = api
        self.camera_index = camera_index
        self.camera_id = camera_id
        self._window: Optional[LiveViewWindow] = None

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Open Live View")
        self.open_btn.clicked.connect(self.toggle)
        row.addWidget(self.open_btn)
        row.addStretch()
        layout.addLayout(row)

        f = QHBoxLayout()
        self.fps_label = QLabel("--")
        self.mean_label = QLabel("--")
        self.max_label = QLabel("--")
        self.fwhm_label = QLabel("--")
        self.guiding_label = QLabel("Not guiding")
        for title, lbl in (("FPS:", self.fps_label), ("Mean:", self.mean_label), ("Max:", self.max_label),
                           ("FWHM:", self.fwhm_label)):
            f.addWidget(QLabel(title))
            lbl.setMinimumWidth(50)
            f.addWidget(lbl)
        f.addStretch()
        layout.addLayout(f)
        g = QHBoxLayout()
        g.addWidget(QLabel("Guiding:"))
        g.addWidget(self.guiding_label, stretch=1)
        layout.addLayout(g)

    @property
    def window_(self) -> LiveViewWindow:
        if self._window is None:
            self._window = LiveViewWindow(self.api, self.camera_index, self.camera_id)
            self._window.roi_selected.connect(self.roi_selected)
            self._window.summary_changed.connect(self._on_summary)
            self._window.visibility_changed.connect(self._on_visibility)
        return self._window

    def toggle(self):
        if self._window is not None and self._window.isVisible():
            self._window.close()
        else:
            self.open_display_next_to_window()

    def open_display_next_to_window(self):
        win = self.window_
        first_show = not win.isVisible()
        win.show()
        win.raise_()
        if first_show:
            main = self.window()
            if main is not None:
                geo = main.frameGeometry()
                win.move(geo.right() + 10, geo.top())

    def is_open(self) -> bool:
        return self._window is not None and self._window.isVisible()

    def stop_guiding(self):
        if self._window is not None:
            self._window.stop_guiding()

    def set_current_subarray_offset(self, hpos: int, vpos: int):
        self.window_.set_subarray_offset(hpos, vpos)

    def _on_visibility(self, visible: bool):
        self.open_btn.setText("Close Live View" if visible else "Open Live View")

    def _on_summary(self, s: dict):
        self.fps_label.setText(f"{s['fps']:.1f}")
        self.mean_label.setText(f"{s['mean']:.0f}")
        self.max_label.setText(f"{s['max']:.0f}")
        if s['fwhm'] is not None:
            self.fwhm_label.setText(f'{s["fwhm"]:.3f}"')
        elif not s['fwhm_ok']:
            self.fwhm_label.setText("fit err")
        else:
            self.fwhm_label.setText("--")
        self.guiding_label.setText(s['guiding'])

    def cleanup(self):
        if self._window is not None:
            self._window.shutdown()
