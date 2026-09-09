"""Main Cerberus PyQt6 application window."""

import logging
import os
import threading
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QTextCursor
from PyQt6.QtWidgets import (QApplication, QDockWidget, QHBoxLayout, QMainWindow, QMessageBox, QPlainTextEdit,
                             QProgressDialog, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from ..config import get_config
from .bridge import QtLogHandler, StateBridge
from .panels import CameraControlsPanel, LiveViewPanel, StatusBar, SubarrayPanel
from .theme import apply_theme
from .windows import CameraSettingsWindow, FocusWindow, TelescopeSettingsWindow
from .workers import run_async, wait_for_tasks

logger = logging.getLogger(__name__)

_LEVEL_COLORS = {logging.DEBUG: "#8a8a8a", logging.INFO: None, logging.WARNING: "#ffb74d",
                 logging.ERROR: "#ef5350", logging.CRITICAL: "#ef5350"}


class CameraTab(QWidget):
    """All per-camera controls: acquisition, subarray, live view summary."""

    def __init__(self, api, camera_index: int, camera_id: str, parent=None):
        super().__init__(parent)
        self.api = api
        self.camera_index = camera_index
        self.camera_id = camera_id

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        self.camera_panel = CameraControlsPanel(api, camera_index, camera_id)
        self.subarray_panel = SubarrayPanel(api, camera_index)
        self.live_panel = LiveViewPanel(api, camera_index, camera_id)
        layout.addWidget(self.camera_panel)
        layout.addWidget(self.subarray_panel)
        layout.addWidget(self.live_panel)
        layout.addStretch()

        # Wiring (mirrors the Tk CameraTab)
        self.camera_panel.streaming_started.connect(self.live_panel.open_display_next_to_window)
        self.camera_panel.streaming_stopped.connect(self.live_panel.stop_guiding)
        self.live_panel.roi_selected.connect(self._on_roi_selected)
        self.subarray_panel.reset_triggered.connect(lambda: self.live_panel.set_current_subarray_offset(0, 0))
        self.subarray_panel.roi_applied.connect(self.live_panel.set_current_subarray_offset)

    def _on_roi_selected(self, hpos: int, vpos: int, hsize: int, vsize: int):
        logger.info(f"[{self.camera_id}] ROI selected: {hsize}x{vsize} at ({hpos}, {vpos})")
        self.subarray_panel.apply_roi(hpos, vpos, hsize, vsize)
        self.live_panel.set_current_subarray_offset(hpos, vpos)

    def update_from_state(self, state):
        cam = state.get_camera(self.camera_index)
        self.camera_panel.update_from_state(state, cam)
        self.subarray_panel.update_from_state(state, cam)

    def cleanup(self):
        self.live_panel.cleanup()


class CerberusQtGUI(QMainWindow):
    """
    Main window: one tab per camera, shared status bar, settings windows,
    a log dock, and non-blocking hardware access via the thread pool.
    """

    def __init__(self, api, cameras: List[Tuple[int, str]], enable_simulation: bool = False,
                 auto_connect: bool = True, dark: bool = True, parent=None):
        super().__init__(parent)
        self.api = api
        self.cameras = cameras
        self.enable_simulation = enable_simulation
        self.auto_connect = auto_connect
        self._dark = dark
        self._closing = False
        self._force_close = False
        self._polling = False
        self._last_state = None

        title = "Cerberus High-Speed Imager - " + ", ".join(c[1] for c in cameras)
        if getattr(api, 'simulated', False):
            title += "  [SIMULATION]"
        self.setWindowTitle(title)

        self._focus_window: Optional[FocusWindow] = None
        self._camera_settings_window: Optional[CameraSettingsWindow] = None
        self._telescope_window: Optional[TelescopeSettingsWindow] = None

        self._build_menu()
        self._build_central()
        self._build_log_dock()

        self.bridge = StateBridge(api, parent=self)
        self.bridge.state_changed.connect(self._apply_state)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(get_config().gui.status_update_interval_ms)
        self._status_timer.timeout.connect(self._poll_status)
        self._status_timer.start()

        self._apply_state(api.state)
        if auto_connect:
            QTimer.singleShot(500, self._auto_connect_hardware)

    # ---- construction -----------------------------------------------------------------

    def _build_menu(self):
        mb = self.menuBar()

        m = mb.addMenu("&File")
        act = QAction("Save configuration", self)
        act.setToolTip("Write calibrated focus positions etc. back to config.json")
        act.triggered.connect(lambda: run_async(self.api._save_config, name="save_config"))
        m.addAction(act)
        m.addSeparator()
        act = QAction("&Quit", self)
        act.setShortcut(QKeySequence.StandardKey.Quit)
        act.triggered.connect(self.close)
        m.addAction(act)

        m = mb.addMenu("&Hardware")
        act = QAction("Connect all", self)
        act.triggered.connect(self._auto_connect_hardware)
        m.addAction(act)
        m.addSeparator()
        self.act_tcs = QAction("Connect telescope", self)
        self.act_tcs.triggered.connect(self._toggle_telescope)
        m.addAction(self.act_tcs)
        self.act_fw = QAction("Connect filter wheel", self)
        self.act_fw.triggered.connect(self._toggle_filterwheel)
        m.addAction(self.act_fw)
        self.act_gps = QAction("Connect GPS timing", self)
        self.act_gps.triggered.connect(self._toggle_gps)
        m.addAction(self.act_gps)
        m.addSeparator()
        for idx, cid in self.cameras:
            act = QAction(f"Connect camera {cid}", self)
            act.triggered.connect(lambda _=False, i=idx: self._toggle_camera(i))
            m.addAction(act)
            setattr(self, f"_act_cam_{idx}", act)

        m = mb.addMenu("&Windows")
        act = QAction("Camera settings…", self)
        act.setShortcut("Ctrl+1")
        act.triggered.connect(self.open_camera_settings)
        m.addAction(act)
        act = QAction("Telescope…", self)
        act.setShortcut("Ctrl+2")
        act.triggered.connect(self.open_telescope_window)
        m.addAction(act)
        act = QAction("Focus…", self)
        act.setShortcut("Ctrl+3")
        act.triggered.connect(self.open_focus_window)
        m.addAction(act)
        act = QAction("Live view (current camera)", self)
        act.setShortcut("Ctrl+4")
        act.triggered.connect(self._open_current_live_view)
        m.addAction(act)
        m.addSeparator()
        self.act_log = QAction("Show log", self)
        self.act_log.setCheckable(True)
        self.act_log.setChecked(True)
        self.act_log.setShortcut("Ctrl+L")
        m.addAction(self.act_log)

        m = mb.addMenu("&View")
        self.act_dark = QAction("Dark theme", self)
        self.act_dark.setCheckable(True)
        self.act_dark.setChecked(self._dark)
        self.act_dark.toggled.connect(self._set_dark)
        m.addAction(self.act_dark)
        act = QAction("Larger font", self)
        act.setShortcut("Ctrl+=")
        act.triggered.connect(lambda: self._change_font(+1))
        m.addAction(act)
        act = QAction("Smaller font", self)
        act.setShortcut("Ctrl+-")
        act.triggered.connect(lambda: self._change_font(-1))
        m.addAction(act)

        m = mb.addMenu("&Help")
        act = QAction("Mouse & keyboard", self)
        act.setShortcut("F1")
        act.triggered.connect(self._show_help)
        m.addAction(act)
        act = QAction("About", self)
        act.triggered.connect(self._show_about)
        m.addAction(act)

    def _build_central(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.tabs = QTabWidget()
        self.camera_tabs: Dict[int, CameraTab] = {}
        for idx, cid in self.cameras:
            tab = CameraTab(self.api, idx, cid)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setWidget(tab)
            self.tabs.addTab(scroll, cid)
            self.camera_tabs[idx] = tab
        layout.addWidget(self.tabs, stretch=1)

        row = QHBoxLayout()
        for text, slot in (("Camera Settings", self.open_camera_settings),
                           ("Telescope", self.open_telescope_window),
                           ("Focus", self.open_focus_window)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        layout.addLayout(row)

        self.status_bar = StatusBar(self.api, self.cameras)
        layout.addWidget(self.status_bar)

    def _build_log_dock(self):
        self.log_dock = QDockWidget("Log", self)
        self.log_dock.setObjectName("logDock")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        self.log_dock.setWidget(self.log_view)
        self.log_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.log_dock.setMinimumHeight(100)
        self.resizeDocks([self.log_dock], [160], Qt.Orientation.Vertical)
        self.act_log.toggled.connect(self.log_dock.setVisible)
        self.log_dock.visibilityChanged.connect(lambda v: self.act_log.setChecked(v) if not self._closing else None)

        self._log_handler = QtLogHandler(logging.INFO)
        self._log_handler.record_ready.connect(self._append_log)
        logging.getLogger().addHandler(self._log_handler)

    def _append_log(self, text: str, level: int):
        color = _LEVEL_COLORS.get(level)
        if color:
            self.log_view.appendHtml(f'<span style="color:{color}">{_escape(text)}</span>')
        else:
            self.log_view.appendPlainText(text)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    # ---- state -------------------------------------------------------------------------

    def _apply_state(self, state):
        if self._closing:
            return
        self._last_state = state
        for tab in self.camera_tabs.values():
            try:
                tab.update_from_state(state)
            except Exception as e:
                logger.debug(f"Tab {tab.camera_id} update error: {e}")
        self.status_bar.update_from_state(state)
        for win in (self._focus_window, self._camera_settings_window, self._telescope_window):
            if win is not None and win.isVisible():
                try:
                    win.update_from_state(state)
                except Exception as e:
                    logger.debug(f"Window update error: {e}")
        self.act_tcs.setText("Disconnect telescope" if state.telescope_connected else "Connect telescope")
        self.act_fw.setText("Disconnect filter wheel" if state.filterwheel_connected else "Connect filter wheel")
        self.act_gps.setText("Disconnect GPS timing" if state.gps_connected else "Connect GPS timing")
        for idx, cid in self.cameras:
            act = getattr(self, f"_act_cam_{idx}", None)
            if act:
                act.setText(f"{'Disconnect' if state.get_camera(idx).connected else 'Connect'} camera {cid}")

    def _poll_status(self):
        """1 Hz hardware poll on a worker thread; skipped if the previous poll is still running."""
        if self._polling or self._closing:
            return
        self._polling = True

        def finished():
            self._polling = False

        run_async(self.api.update_status, on_finally=finished, name="update_status",
                  on_error=lambda msg: logger.error(f"Status update failed: {msg}"))

    # ---- hardware actions ------------------------------------------------------------

    def _auto_connect_hardware(self):
        config = get_config()
        cameras = list(self.cameras)
        api = self.api

        def job():
            state = api.state
            if config.telescope.auto_connect and not state.telescope_connected:
                try:
                    logger.info("Telescope auto-connected" if api.connect_telescope() else "Telescope not available")
                except Exception as e:
                    logger.warning(f"Telescope auto-connect failed: {e}")
            for idx, cid in cameras:
                if api.state.get_camera(idx).connected:
                    continue
                try:
                    ok = api.connect_camera(camera_index=idx)
                    logger.info(f"Camera {cid} {'auto-connected' if ok else 'not available'}")
                except Exception as e:
                    logger.warning(f"Camera {cid} auto-connect failed: {e}")
            if not api.state.filterwheel_connected:
                try:
                    logger.info("Filter wheel auto-connected" if api.connect_filterwheel() else "Filter wheel not available")
                except Exception as e:
                    logger.warning(f"Filter wheel auto-connect failed: {e}")
            if not api.state.gps_connected:
                try:
                    logger.info("GPS timing auto-connected" if api.connect_gps() else "GPS timing not available")
                except Exception as e:
                    logger.warning(f"GPS auto-connect failed: {e}")

        run_async(job, name="auto_connect")

    def _toggle_telescope(self):
        if self.api.state.telescope_connected:
            run_async(self.api.disconnect_telescope, name="disconnect_tcs")
        else:
            run_async(self.api.connect_telescope, name="connect_tcs")

    def _toggle_filterwheel(self):
        if self.api.state.filterwheel_connected:
            run_async(self.api.disconnect_filterwheel, name="disconnect_fw")
        else:
            run_async(self.api.connect_filterwheel, name="connect_fw")

    def _toggle_gps(self):
        if self.api.state.gps_connected:
            run_async(self.api.disconnect_gps, name="disconnect_gps")
        else:
            run_async(self.api.connect_gps, name="connect_gps")

    def _toggle_camera(self, idx: int):
        if self.api.state.get_camera(idx).connected:
            run_async(self.api.disconnect_camera, camera_index=idx, name="disconnect_camera")
        else:
            run_async(self.api.connect_camera, camera_index=idx, name="connect_camera")

    # ---- windows -----------------------------------------------------------------------

    def _current_camera_index(self) -> int:
        i = self.tabs.currentIndex()
        return self.cameras[i][0] if 0 <= i < len(self.cameras) else self.cameras[0][0]

    def open_camera_settings(self):
        if self._camera_settings_window is None:
            self._camera_settings_window = CameraSettingsWindow(self.api, self.cameras)
        self._camera_settings_window.select_camera(self._current_camera_index())
        if self._last_state is not None:
            self._camera_settings_window.update_from_state(self._last_state)
        self._camera_settings_window.show()
        self._camera_settings_window.raise_()

    def open_telescope_window(self):
        if self._telescope_window is None:
            self._telescope_window = TelescopeSettingsWindow(self.api)
        if self._last_state is not None:
            self._telescope_window.update_from_state(self._last_state)
        self._telescope_window.show()
        self._telescope_window.raise_()

    def open_focus_window(self):
        if self._focus_window is None:
            self._focus_window = FocusWindow(self.api, self.cameras, dark=self._dark)
        if self._last_state is not None:
            self._focus_window.update_from_state(self._last_state)
        self._focus_window.show()
        self._focus_window.raise_()

    def _open_current_live_view(self):
        self.camera_tabs[self._current_camera_index()].live_panel.open_display_next_to_window()

    # ---- view ---------------------------------------------------------------------------

    def _set_dark(self, dark: bool):
        self._dark = dark
        app = QApplication.instance()
        apply_theme(app, dark=dark, base_point_size=app.font().pointSize())

    def _change_font(self, delta: int):
        app = QApplication.instance()
        size = max(7, min(20, app.font().pointSize() + delta))
        apply_theme(app, dark=self._dark, base_point_size=size)

    def _show_help(self):
        QMessageBox.information(self, "Mouse & keyboard", """
<b>Live view</b><br>
Wheel: zoom &nbsp; Left-drag: pan &nbsp; F: fit &nbsp; 1: actual pixels<br>
SHIFT+drag: select subarray (ROI)<br>
Right-click: set/track FWHM star<br>
CTRL+click: photometry target &nbsp; ALT+click: comparison star<br>
The "Click" combo makes a plain click do any of these.<br><br>
<b>Main window</b><br>
Ctrl+1 camera settings, Ctrl+2 telescope, Ctrl+3 focus, Ctrl+4 live view, Ctrl+L log, Ctrl+Q quit.<br>
Exposure: type a value and press Enter (or Set). N Frames: leave empty for a continuous stream.<br>
Save: when checked, frames are written to FITS cubes while streaming (auto-resumes after ROI changes).
""")

    def _show_about(self):
        mode = "simulated hardware" if getattr(self.api, 'simulated', False) else "real hardware"
        QMessageBox.about(self, "Cerberus", f"Cerberus High-Speed Imager control (PyQt6)\nRunning with {mode}.")

    # ---- shutdown -------------------------------------------------------------------------

    def closeEvent(self, event):
        if self._force_close:
            event.accept()
            return
        if self._closing:
            event.ignore()
            return
        reply = QMessageBox.question(self, "Quit", "Are you sure you want to quit?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            event.ignore()
            return

        self._closing = True
        event.ignore()
        logger.info("Closing Cerberus GUI...")
        self._status_timer.stop()
        for tab in self.camera_tabs.values():
            tab.cleanup()
        for win in (self._focus_window, self._camera_settings_window, self._telescope_window):
            if win is not None:
                win.close()

        self._progress = QProgressDialog("Stopping cameras and closing connections…", None, 0, 0, self)
        self._progress.setWindowTitle("Shutting down")
        self._progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._progress.setMinimumDuration(0)
        self._progress.show()
        run_async(self._cleanup_api, on_finally=self._finish_close, name="shutdown")

    def _cleanup_api(self):
        api = self.api
        try:
            if api.state.focus_loop_running:
                api.abort_focus_loop()
            for idx in list(api.cameras.keys()):
                cam = api.state.get_camera(idx)
                if cam.streaming:
                    api.stop_streaming(camera_index=idx)   # also stops saving
                elif cam.is_saving:
                    api.stop_saving(camera_index=idx)
                if cam.connected:
                    api.disconnect_camera(camera_index=idx)
            if api.state.telescope_connected:
                api.disconnect_telescope()
            if api.state.filterwheel_connected:
                api.disconnect_filterwheel()
            if api.state.gps_connected:
                api.disconnect_gps()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def _finish_close(self):
        self.bridge.detach()
        logging.getLogger().removeHandler(self._log_handler)
        self._log_handler.close()
        self._progress.close()
        wait_for_tasks(3000)
        self._force_close = True
        self.close()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
