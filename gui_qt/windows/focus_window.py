"""Focus window: manual focus, automated focus loop, per-filter results."""

import logging
import os
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout,
                             QHeaderView, QLabel, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ...config import get_config, observing_night_str
from ..theme import set_status_label
from ..widgets.plot_window import FocusCurveWidget
from ..workers import run_async

logger = logging.getLogger(__name__)


def get_filter_exposure_multiplier(filter_name: str) -> float:
    """Exposure multiplier for a filter (case-insensitive) from config."""
    for key, value in get_config().focusloop.exposure_multipliers.items():
        if key.lower() == filter_name.lower():
            return value
    return 1.0


class FocusWindow(QWidget):
    _progress_sig = pyqtSignal(object)   # FocusLoopProgress, emitted from the focus thread

    def __init__(self, api, cameras: List[Tuple[int, str]], parent=None, dark: bool = True):
        super().__init__(parent, Qt.WindowType.Window)
        self.api = api
        self.cameras = cameras
        self.config = get_config()
        self.setWindowTitle("Focus")
        self.resize(780, 1050)
        self._running = False
        self._filter_checks: Dict[str, QCheckBox] = {}
        self._last_filters: List[str] = []
        self._dark = dark
        self._build()
        self._progress_sig.connect(self._on_progress)

    # ---- UI ----------------------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)
        fmin, fmax = self.config.telescope.focus_min_mm, self.config.telescope.focus_max_mm

        # Manual focus
        manual = QGroupBox("Manual Focus Control")
        g = QGridLayout(manual)
        g.addWidget(QLabel("Current:"), 0, 0)
        self.current_label = QLabel("--")
        self.current_label.setStyleSheet("font-weight: bold; font-size: 13pt;")
        g.addWidget(self.current_label, 0, 1)
        g.addWidget(QLabel("mm"), 0, 2)
        self.focus_status = QLabel("")
        self.focus_status.setObjectName("hint")
        g.addWidget(self.focus_status, 0, 3, 1, 3)

        g.addWidget(QLabel("Go to:"), 1, 0)
        self.goto_spin = QDoubleSpinBox()
        self.goto_spin.setRange(fmin, fmax)
        self.goto_spin.setDecimals(2)
        self.goto_spin.setSingleStep(0.1)
        self.goto_spin.setValue(self.config.gui.default_focus_display_mm)
        g.addWidget(self.goto_spin, 1, 1)
        g.addWidget(QLabel("mm"), 1, 2)
        self.go_btn = QPushButton("Go")
        self.go_btn.clicked.connect(self._on_focus_go)
        g.addWidget(self.go_btn, 1, 3)

        g.addWidget(QLabel("Offset:"), 2, 0)
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(0.01, 20.0)
        self.offset_spin.setDecimals(2)
        self.offset_spin.setSingleStep(0.05)
        self.offset_spin.setValue(0.5)
        g.addWidget(self.offset_spin, 2, 1)
        g.addWidget(QLabel("mm"), 2, 2)
        self.minus_btn = QPushButton("−")
        self.plus_btn = QPushButton("+")
        self.minus_btn.setFixedWidth(40)
        self.plus_btn.setFixedWidth(40)
        self.minus_btn.clicked.connect(lambda: self._on_focus_offset(-1))
        self.plus_btn.clicked.connect(lambda: self._on_focus_offset(+1))
        g.addWidget(self.minus_btn, 2, 3)
        g.addWidget(self.plus_btn, 2, 4)
        self.save_filter_focus_btn = QPushButton("Save current focus for filter")
        self.save_filter_focus_btn.setToolTip("Store the current telescope focus as the calibrated position for the current filter")
        self.save_filter_focus_btn.clicked.connect(self._on_save_filter_focus)
        g.addWidget(self.save_filter_focus_btn, 2, 5)
        g.setColumnStretch(6, 1)
        layout.addWidget(manual)

        # Loop configuration
        cfg = QGroupBox("Focus Loop")
        cl = QGridLayout(cfg)
        fl = self.config.focusloop
        cl.addWidget(QLabel("Camera:"), 0, 0)
        self.camera_combo = QComboBox()
        for _, cid in self.cameras:
            self.camera_combo.addItem(cid)
        cl.addWidget(self.camera_combo, 0, 1)
        cl.addWidget(QLabel("Start (mm):"), 1, 0)
        self.start_spin = self._mm_spin(fl.start_position_mm, fmin, fmax)
        cl.addWidget(self.start_spin, 1, 1)
        cl.addWidget(QLabel("End (mm):"), 1, 2)
        self.end_spin = self._mm_spin(fl.end_position_mm, fmin, fmax)
        cl.addWidget(self.end_spin, 1, 3)
        cl.addWidget(QLabel("Step (mm):"), 1, 4)
        self.step_spin = self._mm_spin(fl.step_size_mm, 0.01, 20.0)
        cl.addWidget(self.step_spin, 1, 5)
        cl.addWidget(QLabel("Base exposure (s):"), 2, 0)
        self.exp_spin = QDoubleSpinBox()
        self.exp_spin.setRange(0.001, 600)
        self.exp_spin.setDecimals(3)
        self.exp_spin.setValue(1.0)
        self.exp_spin.valueChanged.connect(self._update_multiplier_text)
        cl.addWidget(self.exp_spin, 2, 1)
        cl.addWidget(QLabel("Settle (s):"), 2, 2)
        self.settle_spin = QDoubleSpinBox()
        self.settle_spin.setRange(0.0, 60.0)
        self.settle_spin.setDecimals(2)
        self.settle_spin.setValue(fl.settle_time_seconds)
        cl.addWidget(self.settle_spin, 2, 3)
        self.auto_apply_cb = QCheckBox("Apply best focus")
        self.auto_apply_cb.setChecked(fl.auto_apply_best)
        cl.addWidget(self.auto_apply_cb, 2, 4, 1, 2)
        self.positions_label = QLabel("")
        self.positions_label.setObjectName("hint")
        cl.addWidget(self.positions_label, 3, 0, 1, 6)
        for s in (self.start_spin, self.end_spin, self.step_spin):
            s.valueChanged.connect(self._update_positions_label)
        layout.addWidget(cfg)

        # Filters
        fgroup = QGroupBox("Filters (exposure = base × multiplier)")
        self.filter_layout = QHBoxLayout(fgroup)
        self.no_filters_label = QLabel("(connect filter wheel)")
        self.no_filters_label.setObjectName("hint")
        self.filter_layout.addWidget(self.no_filters_label)
        self.filter_layout.addStretch()
        layout.addWidget(fgroup)
        self.multiplier_label = QLabel("")
        self.multiplier_label.setObjectName("hint")
        self.multiplier_label.setWordWrap(True)
        layout.addWidget(self.multiplier_label)

        # Simulation helper
        if getattr(self.api, 'simulated', False):
            sim = QGroupBox("Simulation")
            sl = QHBoxLayout(sim)
            sl.addWidget(QLabel("Simulated best focus:"))
            self.sim_opt_spin = QDoubleSpinBox()
            self.sim_opt_spin.setRange(fmin, fmax)
            self.sim_opt_spin.setDecimals(2)
            self.sim_opt_spin.setValue(self.api.sim_world.optimal_focus_mm)
            self.sim_opt_spin.valueChanged.connect(lambda v: setattr(self.api.sim_world, 'optimal_focus_mm', v))
            sl.addWidget(self.sim_opt_spin)
            sl.addWidget(QLabel("mm"))
            sl.addStretch()
            layout.addWidget(sim)

        # Progress + buttons
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Status:"))
        self.progress_label = QLabel("Idle")
        prow.addWidget(self.progress_label, stretch=1)
        layout.addLayout(prow)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        brow = QHBoxLayout()
        self.run_btn = QPushButton("Run Focus Loop")
        self.run_btn.clicked.connect(self._on_run)
        brow.addWidget(self.run_btn)
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self._on_abort)
        brow.addWidget(self.abort_btn)
        brow.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        brow.addWidget(close)
        layout.addLayout(brow)

        # Results
        rgroup = QGroupBox("Results")
        rl = QVBoxLayout(rgroup)
        self.results_table = QTableWidget(0, 4)
        self.results_table.setHorizontalHeaderLabels(["Filter", "Best focus (mm)", "FWHM (″)", "Status"])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.setMaximumHeight(140)
        rl.addWidget(self.results_table)
        self.curve = FocusCurveWidget(dark=self._dark)
        self.curve.setMinimumHeight(240)
        rl.addWidget(self.curve, stretch=1)
        layout.addWidget(rgroup, stretch=1)

        self._update_positions_label()
        self._update_multiplier_text()

    @staticmethod
    def _mm_spin(value, lo, hi):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(2)
        s.setSingleStep(0.25)
        s.setValue(value)
        return s

    def _update_positions_label(self):
        start, end, step = self.start_spin.value(), self.end_spin.value(), self.step_spin.value()
        if step > 0 and end > start:
            n = int((end - start) / step) + 1
            self.positions_label.setText(f"{n} positions from {start:.2f} to {end:.2f} mm")
        else:
            self.positions_label.setText("Invalid range")

    def _update_multiplier_text(self):
        base = self.exp_spin.value()
        parts = []
        for name in (self._last_filters or list(self.config.filterwheel.filters.values())):
            m = get_filter_exposure_multiplier(name)
            parts.append(f"{name}: {base * m:g}s (×{m:g})")
        self.multiplier_label.setText("Exposures — " + ",  ".join(parts))

    # ---- filters -----------------------------------------------------------------

    def _rebuild_filters(self, filters: List[str]):
        while self.filter_layout.count():
            item = self.filter_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._filter_checks = {}
        if not filters:
            self.no_filters_label = QLabel("(connect filter wheel)")
            self.no_filters_label.setObjectName("hint")
            self.filter_layout.addWidget(self.no_filters_label)
        for name in filters:
            cb = QCheckBox(name)
            cb.setChecked(True)
            self._filter_checks[name] = cb
            self.filter_layout.addWidget(cb)
        self.filter_layout.addStretch()
        self._update_multiplier_text()

    def _selected_filters(self) -> List[str]:
        return [n for n, cb in self._filter_checks.items() if cb.isChecked()]

    # ---- manual focus ----------------------------------------------------------

    def _on_focus_go(self):
        if not self.api.state.telescope_connected:
            self.focus_status.setText("Telescope not connected")
            return
        target = self.goto_spin.value()
        self.focus_status.setText(f"Moving to {target:.2f} mm…")
        run_async(self.api.set_focus, target, name="set_focus",
                  on_done=lambda ok: self.focus_status.setText("" if ok else "TCS rejected focus command"))

    def _on_focus_offset(self, direction: int):
        if not self.api.state.telescope_connected:
            self.focus_status.setText("Telescope not connected")
            return
        offset = self.offset_spin.value() * direction
        self.focus_status.setText(f"Offsetting {offset:+.2f} mm…")
        run_async(self.api.offset_focus, offset, name="offset_focus",
                  on_done=lambda ok: self.focus_status.setText("" if ok else "TCS rejected focus offset"))

    def _on_save_filter_focus(self):
        run_async(self.api.save_current_filter_focus, name="save_filter_focus",
                  on_done=lambda ok: self.focus_status.setText(
                      "Saved focus for current filter" if ok else "Could not save (telescope/filter?)"))

    # ---- focus loop --------------------------------------------------------------

    def _on_run(self):
        if self._running:
            return
        from ...focusloop import FocusLoopConfig

        camera_index, camera_id = self.cameras[self.camera_combo.currentIndex()]
        state = self.api.state
        cam = state.get_camera(camera_index)
        if not cam.connected:
            self.progress_label.setText(f"Error: camera {camera_id} not connected")
            return
        if not state.telescope_connected:
            self.progress_label.setText("Error: telescope not connected")
            return
        if cam.streaming:
            self.progress_label.setText("Error: stop streaming first")
            return
        filters = self._selected_filters()
        if state.filterwheel_connected and self._filter_checks and not filters:
            self.progress_label.setText("Error: no filters selected")
            return

        start, end, step = self.start_spin.value(), self.end_spin.value(), self.step_spin.value()
        base = self.exp_spin.value()
        if end <= start or step <= 0:
            self.progress_label.setText("Error: invalid focus range")
            return

        filter_exposures = {f: base * get_filter_exposure_multiplier(f) for f in filters}
        output_dir = os.path.join(self.config.paths.default_output_dir,
                                  f"captures_{observing_night_str()}", camera_id or f"cam{camera_index}", "focus")
        cfg = FocusLoopConfig(
            start_position=start, end_position=end, step_size=step,
            exposure_time=base, filter_exposures=filter_exposures, filters=filters,
            output_dir=output_dir, camera_id=camera_id, camera_index=camera_index,
            settle_time=self.settle_spin.value(), auto_apply_best=self.auto_apply_cb.isChecked(),
        )
        try:
            cfg.validate()
        except ValueError as e:
            self.progress_label.setText(f"Error: {e}")
            return

        self._running = True
        self.run_btn.setEnabled(False)
        self.abort_btn.setEnabled(True)
        self.progress_bar.setRange(0, cfg.num_positions * max(1, len(filters)))
        self.progress_bar.setValue(0)
        self.progress_label.setText("Starting focus loop…")
        self.results_table.setRowCount(0)
        self.curve.clear()
        logger.info(f"Focus loop: {start}-{end} mm step {step}, base {base}s, filters={filters or 'none'}, out={output_dir}")

        self._completed_before_filter = 0
        run_async(self.api.run_focus_loop, config=cfg, on_progress=self._progress_sig.emit,
                  camera_index=camera_index, on_done=self._on_results,
                  on_error=lambda msg: self.progress_label.setText(f"Error: {msg}"),
                  on_finally=self._loop_finished, name="focus_loop")

    def _on_progress(self, progress):
        filt = f"[{progress.current_filter}] " if progress.current_filter else ""
        self.progress_label.setText(f"{filt}{progress.message}")
        if progress.total_positions:
            self.progress_bar.setValue(min(self.progress_bar.maximum(),
                                           self._completed_before_filter + progress.completed_positions))
            if progress.completed_positions + 1 >= progress.total_positions and "Analyzing" in progress.message:
                self._completed_before_filter += progress.total_positions

    def _on_results(self, results: Optional[dict]):
        if not results:
            self.progress_label.setText("Focus loop failed (see log)")
            return
        self.results_table.setRowCount(len(results))
        for row, (name, res) in enumerate(results.items()):
            self.results_table.setItem(row, 0, QTableWidgetItem(name or "(current)"))
            self.results_table.setItem(row, 1, QTableWidgetItem(f"{res.best_focus:.2f}" if res.success else "--"))
            self.results_table.setItem(row, 2, QTableWidgetItem(f"{res.best_fwhm_arcsec:.3f}" if res.success else "--"))
            self.results_table.setItem(row, 3, QTableWidgetItem("OK" if res.success else (res.error_message or "failed")))
        self.curve.plot_results(results)
        self.progress_bar.setValue(self.progress_bar.maximum())
        if len(results) == 1:
            res = next(iter(results.values()))
            if res.success:
                self.progress_label.setText(f"Done: {res.best_focus:.2f} mm, FWHM={res.best_fwhm_arcsec:.2f}″")
            else:
                self.progress_label.setText(f"Failed: {res.error_message}")
        else:
            ok = sum(1 for r in results.values() if r.success)
            self.progress_label.setText(f"Done: {ok}/{len(results)} filters successful")

    def _loop_finished(self):
        self._running = False
        self.run_btn.setEnabled(True)
        self.abort_btn.setEnabled(False)

    def _on_abort(self):
        self.api.abort_focus_loop()
        self.progress_label.setText("Aborting…")

    # ---- state -----------------------------------------------------------------

    def update_from_state(self, state):
        if state.telescope_focus is not None:
            self.current_label.setText(f"{state.telescope_focus:.2f}")
        else:
            self.current_label.setText("--")
        connected = state.telescope_connected
        for w in (self.go_btn, self.plus_btn, self.minus_btn, self.save_filter_focus_btn):
            w.setEnabled(connected)

        if state.filterwheel_connected and state.available_filters:
            if list(state.available_filters) != self._last_filters:
                self._last_filters = list(state.available_filters)
                self._rebuild_filters(self._last_filters)
        elif not state.filterwheel_connected and self._last_filters:
            self._last_filters = []
            self._rebuild_filters([])

        if state.focus_loop_running and not self._running:
            self.run_btn.setEnabled(False)
            self.abort_btn.setEnabled(True)
        elif not state.focus_loop_running and not self._running:
            self.run_btn.setEnabled(True)
            self.abort_btn.setEnabled(False)
