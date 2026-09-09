"""Matplotlib-in-Qt plot windows: live FWHM history, live lightcurve, focus curve."""

import csv
import logging
import time
from typing import Callable, List, Optional

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget, QCheckBox, QSpinBox)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

logger = logging.getLogger(__name__)


def _style_axes(ax, dark: bool):
    fg = "#dddddd" if dark else "#222222"
    bg = "#2b2b2b" if dark else "#ffffff"
    ax.set_facecolor(bg)
    ax.figure.set_facecolor(bg)
    for spine in ax.spines.values():
        spine.set_color(fg)
    ax.tick_params(colors=fg, labelsize=9)
    ax.xaxis.label.set_color(fg)
    ax.yaxis.label.set_color(fg)
    ax.title.set_color(fg)
    ax.grid(True, alpha=0.3)


class _PlotWindowBase(QWidget):
    def __init__(self, title: str, parent=None, dark: bool = True):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.resize(900, 520)
        self._dark = dark
        self.fig = Figure(figsize=(8, 4), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, stretch=1)
        ctrl = QHBoxLayout()
        self.live_cb = QCheckBox("Live update")
        self.live_cb.setChecked(True)
        ctrl.addWidget(self.live_cb)
        ctrl.addWidget(QLabel("Window (s):"))
        self.window_spin = QSpinBox()
        self.window_spin.setRange(0, 86400)
        self.window_spin.setValue(0)
        self.window_spin.setSpecialValueText("all")
        self.window_spin.setToolTip("Show only the last N seconds (0 = all)")
        ctrl.addWidget(self.window_spin)
        self.stats_label = QLabel("")
        ctrl.addWidget(self.stats_label, stretch=1)
        self.save_btn = QPushButton("Save CSV…")
        self.save_btn.clicked.connect(self._save_csv)
        ctrl.addWidget(self.save_btn)
        layout.addLayout(ctrl)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        if self.live_cb.isChecked() and self.isVisible():
            self.refresh()

    def refresh(self):
        raise NotImplementedError

    def _save_csv(self):
        raise NotImplementedError

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)


class TimeSeriesPlotWindow(_PlotWindowBase):
    """Live plot of (timestamp, value) pairs - used for FWHM history."""

    def __init__(self, title: str, ylabel: str, data_source: Callable[[], List[tuple]],
                 parent=None, dark: bool = True, value_format: str = "{:.3f}"):
        super().__init__(title, parent, dark)
        self._source = data_source
        self._ylabel = ylabel
        self._fmt = value_format
        self.ax = self.fig.add_subplot(111)
        _style_axes(self.ax, dark)
        (self.line,) = self.ax.plot([], [], '-', color="#64b5f6", linewidth=1, marker='o', markersize=2)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel(ylabel)
        self.ax.set_title(title)
        self.refresh()

    def refresh(self):
        data = self._source()
        if not data:
            self.stats_label.setText("no data")
            self.canvas.draw_idle()
            return
        t0 = data[0][0]
        window = self.window_spin.value()
        if window > 0:
            cutoff = time.time() - window
            data = [d for d in data if d[0] >= cutoff] or data[-1:]
        ts = np.array([d[0] - t0 for d in data])
        ys = np.array([d[1] for d in data], dtype=float)
        self.line.set_data(ts, ys)
        self.ax.relim()
        self.ax.autoscale_view()
        f = self._fmt.format
        self.stats_label.setText(f"n={len(ys)}  mean={f(ys.mean())}  std={f(ys.std())}  "
                                 f"min={f(ys.min())}  max={f(ys.max())}  last={f(ys[-1])}")
        self.canvas.draw_idle()

    def _save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["unix_time", self._ylabel])
            for t, v in self._source():
                w.writerow([f"{t:.6f}", f"{v:.6f}"])
        logger.info(f"Saved {path}")


class LightcurvePlotWindow(_PlotWindowBase):
    """Live aperture-photometry plot: raw fluxes on top, relative flux below."""

    def __init__(self, data_source: Callable[[], List[dict]], parent=None, dark: bool = True):
        super().__init__("Live Lightcurve", parent, dark)
        self._source = data_source
        self.ax1 = self.fig.add_subplot(211)
        self.ax2 = self.fig.add_subplot(212, sharex=self.ax1)
        for ax in (self.ax1, self.ax2):
            _style_axes(ax, dark)
        (self.l_t,) = self.ax1.plot([], [], '-', color="#4caf50", linewidth=1, label="Target")
        (self.l_c,) = self.ax1.plot([], [], '-', color="#00bcd4", linewidth=1, label="Comparison")
        (self.l_r,) = self.ax2.plot([], [], '.', color="#64b5f6", markersize=3, linestyle='-', linewidth=0.7)
        self.ax1.set_ylabel("Raw flux (ADU)")
        self.ax1.set_title("Aperture photometry")
        self.ax1.legend(loc="upper right", fontsize=8)
        self.ax2.set_xlabel("Time (s)")
        self.ax2.set_ylabel("Relative flux (T/C)")
        self.refresh()

    def refresh(self):
        data = self._source()
        if not data:
            self.stats_label.setText("no data")
            self.canvas.draw_idle()
            return
        t0 = data[0]['time']
        window = self.window_spin.value()
        if window > 0:
            cutoff = time.time() - window
            data = [d for d in data if d['time'] >= cutoff] or data[-1:]

        def series(key):
            pts = [(d['time'] - t0, d[key]) for d in data if d[key] is not None]
            if not pts:
                return np.array([]), np.array([])
            a = np.array(pts)
            return a[:, 0], a[:, 1]

        self.l_t.set_data(*series('target_flux'))
        self.l_c.set_data(*series('comp_flux'))
        tr, rel = series('relative_flux')
        self.l_r.set_data(tr, rel)
        for ax in (self.ax1, self.ax2):
            ax.relim()
            ax.autoscale_view()
        if rel.size:
            m, s = rel.mean(), rel.std()
            pct = (s / m * 100) if m else float('nan')
            self.stats_label.setText(f"n={rel.size}  rel mean={m:.4f}  std={s:.4f} ({pct:.2f}%)")
        else:
            tt, tf = series('target_flux')
            self.stats_label.setText(f"n={tf.size}  target mean={tf.mean():.0f}" if tf.size else "no data")
        self.canvas.draw_idle()

    def _save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["unix_time", "target_flux", "comp_flux", "relative_flux"])
            for d in self._source():
                w.writerow([f"{d['time']:.6f}", d['target_flux'], d['comp_flux'], d['relative_flux']])
        logger.info(f"Saved {path}")


class FocusCurveWidget(QWidget):
    """Embedded focus-curve plot (measurements + fitted parabola)."""

    def __init__(self, parent=None, dark: bool = True):
        super().__init__(parent)
        self.fig = Figure(figsize=(5, 3), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.fig)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self.ax = self.fig.add_subplot(111)
        _style_axes(self.ax, dark)
        self.ax.set_xlabel("Focus (mm)")
        self.ax.set_ylabel("FWHM (arcsec)")
        self._dark = dark
        self.clear()

    def clear(self):
        self.ax.cla()
        _style_axes(self.ax, self._dark)
        self.ax.set_xlabel("Focus (mm)")
        self.ax.set_ylabel("FWHM (arcsec)")
        self.ax.set_title("Focus curve")
        self.canvas.draw_idle()

    def plot_results(self, results: dict):
        """results: {filter_name_or_None: FocusResult}"""
        self.ax.cla()
        _style_axes(self.ax, self._dark)
        colors = ["#64b5f6", "#4caf50", "#ff9800", "#e91e63", "#9c27b0", "#00bcd4", "#cddc39", "#ff5722"]
        for i, (name, res) in enumerate(results.items()):
            if not res or not res.measurements:
                continue
            color = colors[i % len(colors)]
            xs = np.array(sorted(res.measurements))
            ys = np.array([res.measurements[x] for x in xs])
            label = name or "all"
            self.ax.plot(xs, ys, 'o', color=color, label=f"{label} data")
            if res.success and res.fit_coefficients and any(res.fit_coefficients):
                a, b, c = res.fit_coefficients
                xf = np.linspace(xs.min(), xs.max(), 200)
                self.ax.plot(xf, a * xf ** 2 + b * xf + c, '-', color=color, alpha=0.8,
                             label=f"{label} fit: {res.best_focus:.2f} mm, {res.best_fwhm_arcsec:.2f}\"")
                self.ax.axvline(res.best_focus, color=color, linestyle='--', alpha=0.5)
        self.ax.set_xlabel("Focus (mm)")
        self.ax.set_ylabel("FWHM (arcsec)")
        self.ax.set_title("Focus curve")
        self.ax.legend(fontsize=8)
        self.canvas.draw_idle()

    def plot_partial(self, measurements: dict, label: str = ""):
        """Live update while a run is in progress."""
        self.ax.cla()
        _style_axes(self.ax, self._dark)
        if measurements:
            xs = np.array(sorted(measurements))
            ys = np.array([measurements[x] for x in xs])
            self.ax.plot(xs, ys, 'o-', color="#64b5f6", label=label or "in progress")
            self.ax.legend(fontsize=8)
        self.ax.set_xlabel("Focus (mm)")
        self.ax.set_ylabel("FWHM (arcsec)")
        self.ax.set_title("Focus curve (running)")
        self.canvas.draw_idle()
