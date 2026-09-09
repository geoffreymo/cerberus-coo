"""Camera controls panel: connection, exposure, streaming, saving, filter."""

import logging
import os
import time
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDoubleValidator, QIntValidator
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from ...config import get_config
from ..theme import COLOR_ERR, set_status_label
from ..workers import run_async

if TYPE_CHECKING:
    from ...api import CerberusAPI

logger = logging.getLogger(__name__)

_UNIT_TO_SEC = {"ms": 1e-3, "s": 1.0, "min": 60.0}


def format_exposure(value: float) -> str:
    """Format an exposure value for display without excessive decimals."""
    if value >= 1.0 and value == int(value):
        return str(int(value))
    if value >= 1.0:
        return f"{value:.3f}".rstrip('0').rstrip('.')
    return f"{value:.6g}"


def _hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    return f


class CameraControlsPanel(QGroupBox):
    """Per-camera acquisition controls (mirrors the Tk CameraControlsPanel)."""

    streaming_started = pyqtSignal()
    streaming_stopped = pyqtSignal()
    _nframes_reached = pyqtSignal()     # emitted from the camera thread

    def __init__(self, api: 'CerberusAPI', camera_index: int, camera_id: str, parent: QWidget = None):
        super().__init__("Camera Controls", parent)
        self.api = api
        self.camera_index = camera_index
        self.camera_id = camera_id
        self.config = get_config()

        self._unit = "ms"
        self._busy = False            # an async connect/start/stop is in flight
        self._taking_images = False
        self._target_frames = 0
        self._stopping = False
        self._save_pending = False
        self._stream_start_time = 0.0
        self._last_state = None

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_stream_timer)

        # Frame counting for N-frames mode: the controller callback fires per frame
        # (throttled by _callback_skip at very high rates), which is far quicker
        # than waiting for the 1 Hz status poll.
        self._frames_seen = 0
        self._nframes_emitted = False
        self._nframes_reached.connect(self._on_nframes_reached)
        controller = self.api.cameras.get(camera_index)
        if controller is not None and hasattr(controller, 'on_frame'):
            controller.on_frame(self._on_controller_frame)

        self._build()

    # ---- UI ------------------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Connection row
        row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect)
        row.addWidget(self.connect_btn)
        self.status_label = QLabel("Disconnected")
        set_status_label(self.status_label, "Disconnected", "muted")
        row.addWidget(self.status_label)
        row.addStretch()
        self.fps_label = QLabel("")
        self.fps_label.setObjectName("hint")
        row.addWidget(self.fps_label)
        layout.addLayout(row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        r = 0
        grid.addWidget(QLabel("Target:"), r, 0)
        self.target_edit = QLineEdit(self.config.gui.default_object_name)
        grid.addWidget(self.target_edit, r, 1)
        grid.addWidget(QLabel("Filter:"), r, 2)
        self.filter_combo = QComboBox()
        self.filter_combo.setMinimumWidth(90)
        self.filter_combo.activated.connect(self._on_filter_activated)
        grid.addWidget(self.filter_combo, r, 3)
        r += 1
        grid.addWidget(QLabel("Comment:"), r, 0)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("FITS COMMENT (optional)")
        grid.addWidget(self.comment_edit, r, 1, 1, 3)
        layout.addLayout(grid)

        layout.addWidget(_hline())

        # Exposure / N frames
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        r = 0
        grid.addWidget(QLabel("Exposure:"), r, 0)
        exp_row = QHBoxLayout()
        self.exposure_edit = QLineEdit("1000")
        self.exposure_edit.setValidator(QDoubleValidator(0.0, 1e9, 6))
        self.exposure_edit.setMaximumWidth(110)
        self.exposure_edit.setToolTip("Press Enter to apply")
        self.exposure_edit.returnPressed.connect(self._on_exposure_apply)
        exp_row.addWidget(self.exposure_edit)
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["ms", "s", "min"])
        self.unit_combo.activated.connect(self._on_unit_change)
        exp_row.addWidget(self.unit_combo)
        self.exposure_apply_btn = QPushButton("Set")
        self.exposure_apply_btn.setMaximumWidth(50)
        self.exposure_apply_btn.clicked.connect(self._on_exposure_apply)
        exp_row.addWidget(self.exposure_apply_btn)
        exp_row.addStretch()
        grid.addLayout(exp_row, r, 1)

        grid.addWidget(QLabel("N Frames:"), r, 2)
        self.nframes_edit = QLineEdit("")
        self.nframes_edit.setValidator(QIntValidator(1, 100000000))
        self.nframes_edit.setMaximumWidth(90)
        self.nframes_edit.setPlaceholderText("continuous")
        self.nframes_edit.setToolTip("Leave empty for a continuous stream; otherwise streaming stops after N frames")
        grid.addWidget(self.nframes_edit, r, 3)
        r += 1
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("hint")
        grid.addWidget(self.progress_label, r, 2, 1, 2)

        grid.addWidget(QLabel("Readout:"), r, 0)
        self.readout_combo = QComboBox()
        self.readout_combo.addItems(["Ultra Quiet", "Standard"])
        self.readout_combo.activated.connect(self._on_readout_activated)
        grid.addWidget(self.readout_combo, r, 1)
        layout.addLayout(grid)

        # Save / Start row
        row = QHBoxLayout()
        self.save_cb = QCheckBox("Save")
        self.save_cb.setObjectName("saving")
        self.save_cb.setToolTip("Save frames to FITS cubes while streaming")
        self.save_cb.toggled.connect(self._on_save_toggle)
        row.addWidget(self.save_cb)
        row.addStretch()
        self.stream_timer_label = QLabel("")
        row.addWidget(self.stream_timer_label)
        row.addSpacing(10)
        row.addWidget(QLabel("Frames:"))
        self.frames_label = QLabel("0")
        self.frames_label.setMinimumWidth(60)
        row.addWidget(self.frames_label)
        row.addStretch()
        self.start_stop_btn = QPushButton("Start")
        self.start_stop_btn.setObjectName("startStop")
        self.start_stop_btn.setProperty("streaming", "false")
        self.start_stop_btn.setMinimumWidth(100)
        self.start_stop_btn.setEnabled(False)
        self.start_stop_btn.clicked.connect(self._on_start_stop)
        row.addWidget(self.start_stop_btn)
        layout.addLayout(row)

        layout.addWidget(_hline())

        # Save path / cube size
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.addWidget(QLabel("Save Path:"), 0, 0)
        self.output_dir_edit = QLineEdit(self.config.paths.default_output_dir)
        grid.addWidget(self.output_dir_edit, 0, 1, 1, 2)
        browse = QPushButton("…")
        browse.setMaximumWidth(36)
        browse.clicked.connect(self._browse_directory)
        grid.addWidget(browse, 0, 3)
        grid.addWidget(QLabel("Cube Size:"), 1, 0)
        cube_row = QHBoxLayout()
        self.cube_spin = QSpinBox()
        self.cube_spin.setRange(1, 1000000)
        self.cube_spin.setValue(int(self.config.acquisition.frames_per_cube))
        self.cube_spin.setMaximumWidth(110)
        cube_row.addWidget(self.cube_spin)
        cube_row.addWidget(QLabel("frames"))
        cube_row.addStretch()
        grid.addLayout(cube_row, 1, 1)
        stats = QHBoxLayout()
        stats.addWidget(QLabel("Saved:"))
        self.saved_label = QLabel("0")
        self.saved_label.setMinimumWidth(50)
        stats.addWidget(self.saved_label)
        stats.addWidget(QLabel("Cubes:"))
        self.cubes_label = QLabel("0")
        self.cubes_label.setMinimumWidth(30)
        stats.addWidget(self.cubes_label)
        stats.addWidget(QLabel("Drop:"))
        self.dropped_label = QLabel("0")
        self.dropped_label.setMinimumWidth(40)
        stats.addWidget(self.dropped_label)
        stats.addStretch()
        grid.addLayout(stats, 1, 2, 1, 2)
        layout.addLayout(grid)

    # ---- helpers ---------------------------------------------------------------

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.connect_btn.setEnabled(not busy)
        if self._last_state is not None:
            self.update_from_state(self._last_state)

    def _set_streaming_button(self, streaming: bool):
        self.start_stop_btn.setText("Stop" if streaming else "Start")
        self.start_stop_btn.setProperty("streaming", "true" if streaming else "false")
        self.start_stop_btn.style().unpolish(self.start_stop_btn)
        self.start_stop_btn.style().polish(self.start_stop_btn)

    def _exposure_seconds(self) -> Optional[float]:
        try:
            value = float(self.exposure_edit.text())
        except ValueError:
            return None
        return value * _UNIT_TO_SEC[self.unit_combo.currentText()]

    def _show_exposure(self, seconds: float):
        unit = self.unit_combo.currentText()
        self.exposure_edit.setText(format_exposure(seconds / _UNIT_TO_SEC[unit]))

    def _browse_directory(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.output_dir_edit.text())
        if d:
            self.output_dir_edit.setText(d)

    # ---- actions ---------------------------------------------------------------

    def _on_connect(self):
        if self._busy:
            return
        cam = self.api.state.get_camera(self.camera_index)
        if cam.connected:
            self._set_busy(True)
            set_status_label(self.status_label, "Disconnecting…", "warn")
            run_async(self.api.disconnect_camera, camera_index=self.camera_index,
                      on_finally=lambda: self._set_busy(False), name="disconnect_camera")
        else:
            self._set_busy(True)
            set_status_label(self.status_label, "Connecting…", "warn")

            def done(ok):
                if ok:
                    exp = self.api.get_exposure(camera_index=self.camera_index)
                    if exp:
                        self._show_exposure(exp)
                else:
                    set_status_label(self.status_label, "Failed", "err")

            run_async(self.api.connect_camera, camera_index=self.camera_index,
                      on_done=done, on_finally=lambda: self._set_busy(False), name="connect_camera")

    def _on_start_stop(self):
        if self._busy:
            return
        cam = self.api.state.get_camera(self.camera_index)
        if cam.streaming:
            self.stop_streaming()
        else:
            self.start_streaming()

    def start_streaming(self):
        n_text = self.nframes_edit.text().strip()
        n_images = int(n_text) if n_text else 0
        self._set_busy(True)
        self.start_stop_btn.setEnabled(False)
        self.start_stop_btn.setText("Starting…")

        def done(ok):
            if not ok:
                logger.error(f"[{self.camera_id}] start_streaming failed")
                self.progress_label.setText("Start failed")
                return
            self._stream_start_time = time.time()
            self._update_stream_timer()
            self._timer.start()
            self._stopping = False
            self._frames_seen = 0
            self._nframes_emitted = False
            if n_images > 0:
                # start_streaming() resets frames_captured, so count from zero (fixes BUG #45)
                self._taking_images = True
                self._target_frames = n_images
                self.progress_label.setText(f"Taking: 0 / {n_images}")
            else:
                self._taking_images = False
                self.progress_label.setText("")
            self.streaming_started.emit()
            if self.save_cb.isChecked():
                self._start_saving()

        run_async(self.api.start_streaming, camera_index=self.camera_index,
                  on_done=done, on_finally=lambda: self._set_busy(False), name="start_streaming")

    def stop_streaming(self):
        if self._stopping:
            return
        self._stopping = True
        self._set_busy(True)
        self.start_stop_btn.setEnabled(False)
        self.start_stop_btn.setText("Stopping…")
        self._timer.stop()
        self.stream_timer_label.setText("")
        self.streaming_stopped.emit()   # lets the live view stop guiding immediately

        def do_stop():
            # stop_streaming() also stops saving after a short grace period
            self.api.stop_streaming(camera_index=self.camera_index)

        def finished():
            self._stopping = False
            if self._taking_images:
                self._taking_images = False
            self._set_busy(False)

        run_async(do_stop, on_finally=finished, name="stop_streaming")

    def _on_controller_frame(self, frame, timestamp, framestamp):
        """Camera-thread callback: count frames for N-frames mode."""
        if not self._taking_images or self._nframes_emitted:
            return
        controller = self.api.cameras.get(self.camera_index)
        self._frames_seen += max(1, int(getattr(controller, '_callback_skip', 1) or 1))
        if self._frames_seen >= self._target_frames:
            self._nframes_emitted = True
            self._nframes_reached.emit()

    def _on_nframes_reached(self):
        if self._taking_images and not self._stopping:
            self._taking_images = False
            self.progress_label.setText(f"Done: {self._target_frames} images")
            self.stop_streaming()

    def _on_exposure_apply(self):
        seconds = self._exposure_seconds()
        if seconds is None or seconds <= 0:
            return
        cam = self.api.state.get_camera(self.camera_index)
        if not cam.connected:
            return
        run_async(self.api.set_exposure, seconds, camera_index=self.camera_index, name="set_exposure",
                  on_done=lambda ok: None if ok else logger.error(f"[{self.camera_id}] set_exposure failed"))
        self.exposure_edit.clearFocus()

    def _on_unit_change(self, index: int):
        new_unit = self.unit_combo.itemText(index)
        try:
            value = float(self.exposure_edit.text())
        except ValueError:
            self._unit = new_unit
            return
        seconds = value * _UNIT_TO_SEC[self._unit]
        self._unit = new_unit
        self.exposure_edit.setText(format_exposure(seconds / _UNIT_TO_SEC[new_unit]))

    def _on_filter_activated(self, index: int):
        name = self.filter_combo.itemText(index)
        if not name or not self.api.state.filterwheel_connected:
            return
        self.filter_combo.setEnabled(False)
        run_async(self.api.set_filter, name, name="set_filter",
                  on_finally=lambda: self.filter_combo.setEnabled(True))

    def _on_readout_activated(self, index: int):
        cam = self.api.state.get_camera(self.camera_index)
        if not cam.connected:
            return
        value = {0: 1.0, 1: 2.0}[index]   # READOUT_SPEED: 1 = Ultra Quiet, 2 = Standard
        run_async(self.api.set_camera_property, "READOUT_SPEED", value,
                  camera_index=self.camera_index, name="set_readout")

    def _on_save_toggle(self, checked: bool):
        cam = self.api.state.get_camera(self.camera_index)
        if checked:
            if cam.streaming and not cam.is_saving:
                self._start_saving()
        else:
            if cam.is_saving:
                self._save_pending = True
                run_async(self.api.stop_saving, camera_index=self.camera_index, name="stop_saving",
                          on_finally=lambda: setattr(self, '_save_pending', False))

    def _start_saving(self):
        if self._save_pending:
            return
        output_dir = self.output_dir_edit.text().strip()
        object_name = self.target_edit.text().strip() or "Object"
        comment = self.comment_edit.text().strip()
        frames_per_cube = self.cube_spin.value()
        if not output_dir:
            QMessageBox.warning(self, "Save", "Please choose an output directory.")
            self.save_cb.setChecked(False)
            return
        if not os.path.isdir(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
                logger.info(f"Created output directory: {output_dir}")
            except Exception as e:
                QMessageBox.critical(self, "Directory Error",
                                     f"Cannot create output directory:\n{output_dir}\n\n{e}")
                self.save_cb.setChecked(False)
                return
        ok = self.api.start_saving(object_name=object_name, output_dir=output_dir,
                                   frames_per_cube=frames_per_cube, comment=comment,
                                   camera_index=self.camera_index)
        if not ok:
            logger.warning(f"[{self.camera_id}] start_saving failed; unchecking Save")
            self.save_cb.blockSignals(True)
            self.save_cb.setChecked(False)
            self.save_cb.blockSignals(False)

    # ---- timers / state ---------------------------------------------------------

    def _update_stream_timer(self):
        if self._stream_start_time <= 0:
            return
        elapsed = int(time.time() - self._stream_start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        if h:
            text = f"Streaming: {h:02d}h {m:02d}m {s:02d}s"
        elif m:
            text = f"Streaming: {m:02d}m {s:02d}s"
        else:
            text = f"Streaming: {s:02d}s"
        self.stream_timer_label.setText(text)

    def update_from_state(self, state, cam_state=None):
        self._last_state = state
        cam = cam_state or state.get_camera(self.camera_index)

        if cam.connected:
            self.connect_btn.setText("Disconnect")
            if not self._busy:
                set_status_label(self.status_label, "Connected", "ok")
            self.start_stop_btn.setEnabled(not self._busy)
            if not self._busy:
                self._set_streaming_button(cam.streaming)
        else:
            self.connect_btn.setText("Connect")
            if not self._busy:
                set_status_label(self.status_label, "Disconnected", "muted")
            self.start_stop_btn.setEnabled(False)
            if not self._busy:
                self._set_streaming_button(False)

        # Streaming timer bookkeeping when streaming stopped elsewhere (API/other window)
        if not cam.streaming and self._timer.isActive() and not self._busy:
            self._timer.stop()
            self.stream_timer_label.setText("")
        if cam.streaming and not self._timer.isActive() and not self._busy:
            self._stream_start_time = self._stream_start_time or time.time()
            self._timer.start()

        # Filters (shared)
        if state.filterwheel_connected and state.available_filters:
            current_items = [self.filter_combo.itemText(i) for i in range(self.filter_combo.count())]
            if current_items != list(state.available_filters):
                self.filter_combo.blockSignals(True)
                self.filter_combo.clear()
                self.filter_combo.addItems(state.available_filters)
                self.filter_combo.blockSignals(False)
            if state.current_filter and state.current_filter != self.filter_combo.currentText():
                idx = self.filter_combo.findText(state.current_filter)
                self.filter_combo.blockSignals(True)
                if idx >= 0:
                    self.filter_combo.setCurrentIndex(idx)
                self.filter_combo.blockSignals(False)
            self.filter_combo.setToolTip(f"Current: {state.current_filter}")
        else:
            if self.filter_combo.count():
                self.filter_combo.blockSignals(True)
                self.filter_combo.clear()
                self.filter_combo.blockSignals(False)
            self.filter_combo.setToolTip("Filter wheel not connected")

        # Counters
        self.frames_label.setText(str(cam.frames_captured))
        if cam.streaming and cam.frame_rate:
            self.fps_label.setText(f"{cam.frame_rate:.1f} fps")
        else:
            self.fps_label.setText("")

        # N-frames progress (the stop itself is triggered by the frame callback;
        # this is the fallback in case callbacks are throttled)
        if self._taking_images and cam.streaming and not self._stopping:
            self.progress_label.setText(f"Taking: {cam.frames_captured} / {self._target_frames}")
            if cam.frames_captured >= self._target_frames:
                self._on_nframes_reached()
        elif not self._taking_images and not cam.streaming and self.progress_label.text().startswith("Done"):
            self.progress_label.setText(f"Done: {cam.frames_captured} images")

        # Exposure (don't fight the user while typing)
        if cam.exposure and not self.exposure_edit.hasFocus():
            shown = format_exposure(cam.exposure / _UNIT_TO_SEC[self.unit_combo.currentText()])
            if self.exposure_edit.text() != shown:
                self.exposure_edit.setText(shown)

        # Readout combo from params
        readout = str(cam.params.get('READOUT SPEED', '')).upper()
        if readout:
            want = 0 if 'ULTRA' in readout else 1
            if self.readout_combo.currentIndex() != want:
                self.readout_combo.blockSignals(True)
                self.readout_combo.setCurrentIndex(want)
                self.readout_combo.blockSignals(False)

        # Save checkbox highlight + auto-resume when streaming restarted (e.g. after ROI change)
        self.save_cb.setProperty("active", "true" if cam.is_saving else "false")
        self.save_cb.style().unpolish(self.save_cb)
        self.save_cb.style().polish(self.save_cb)
        if (self.save_cb.isChecked() and cam.streaming and not cam.is_saving
                and not self._busy and not self._save_pending and not self._stopping):
            self._start_saving()

        self.saved_label.setText(str(cam.frames_saved))
        self.cubes_label.setText(str(cam.cubes_saved))
        self.dropped_label.setText(str(cam.frames_dropped))
        self.dropped_label.setStyleSheet(f"color: {COLOR_ERR}; font-weight: bold;" if cam.frames_dropped > 0 else "")
