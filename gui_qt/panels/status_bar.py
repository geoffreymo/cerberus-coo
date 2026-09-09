"""Status bar: one line per camera, plus TCS / filter wheel / GPS."""

from typing import Dict, List, Tuple

from PyQt6.QtWidgets import QGridLayout, QLabel, QWidget

from ..theme import COLOR_INFO, COLOR_MUTED, COLOR_OK, COLOR_WARN


def _cell(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("statusCell")
    return lbl


def _color(label: QLabel, color: str, bold: bool = False):
    label.setStyleSheet(f"QLabel#statusCell {{ color: {color}; {'font-weight: bold;' if bold else ''} }}")


class StatusBar(QWidget):
    def __init__(self, api, cameras: List[Tuple[int, str]], parent=None):
        super().__init__(parent)
        self.api = api
        self.cameras = cameras
        self.camera_labels: Dict[int, QLabel] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(2)
        row = 0
        for idx, cid in cameras:
            lbl = _cell(f"{cid}: Disconnected")
            _color(lbl, COLOR_MUTED)
            grid.addWidget(lbl, row, 0, 1, 3)
            self.camera_labels[idx] = lbl
            row += 1
        self.tcs_label = _cell("TCS: Disconnected")
        self.filter_label = _cell("Filter: Disconnected")
        self.gps_label = _cell("GPS: ○")
        for i, lbl in enumerate((self.tcs_label, self.filter_label, self.gps_label)):
            _color(lbl, COLOR_MUTED)
            grid.addWidget(lbl, row, i)
            grid.setColumnStretch(i, 1)

    def update_from_state(self, state):
        for idx, cid in self.cameras:
            cam = state.get_camera(idx)
            lbl = self.camera_labels[idx]
            if cam.connected:
                if cam.streaming:
                    status = f"Saving ({cam.frames_saved})" if cam.is_saving else "Streaming"
                    color = COLOR_WARN if cam.is_saving else COLOR_OK
                else:
                    status, color = "Connected", COLOR_INFO
                temp = f"  {cam.temperature:.1f}°C" if cam.temperature is not None else ""
                fps = f"  {cam.frame_rate:.1f} fps" if (cam.streaming and cam.frame_rate) else ""
                lbl.setText(f"{cid}: {status}{temp}{fps}")
                _color(lbl, color, bold=cam.streaming)
            else:
                lbl.setText(f"{cid}: Disconnected")
                _color(lbl, COLOR_MUTED)

        if state.telescope_connected:
            focus = f"{state.telescope_focus:.2f} mm" if state.telescope_focus is not None else "--"
            self.tcs_label.setText(f"TCS: focus {focus}")
            _color(self.tcs_label, COLOR_OK)
        else:
            self.tcs_label.setText("TCS: Disconnected")
            _color(self.tcs_label, COLOR_MUTED)

        if state.filterwheel_connected:
            self.filter_label.setText(f"Filter: {state.current_filter or '--'}")
            _color(self.filter_label, COLOR_OK)
        else:
            self.filter_label.setText("Filter: Disconnected")
            _color(self.filter_label, COLOR_MUTED)

        if state.gps_connected:
            self.gps_label.setText("GPS: ●")
            _color(self.gps_label, COLOR_OK)
        else:
            self.gps_label.setText("GPS: ○")
            _color(self.gps_label, COLOR_MUTED)
