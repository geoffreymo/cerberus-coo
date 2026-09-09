"""Matplotlib-in-Qt plot windows: live FWHM history, live lightcurve, focus curve."""

import csv
import logging
import time
from typing import Callable, List, Optional

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
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
    """
    Embedded focus-curve plot (measurements + fitted parabola, one colour per filter).

    Uses fixed subplot margins rather than tight_layout: with a legend taller than
    the axes, tight_layout collapses the axes to a few pixels after a resize.
    """

    COLORS = ["#64b5f6", "#4caf50", "#ff9800", "#e91e63", "#9c27b0", "#00bcd4", "#cddc39", "#ff5722"]

    def __init__(self, parent=None, dark: bool = True):
        super().__init__(parent)
        self._dark = dark
        self.fig = Figure(figsize=(6, 3.2))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setMinimumHeight(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self.ax = self.fig.add_subplot(111)
        self.clear()

    def _setup_axes(self, title: str, with_legend: bool):
        self.ax.cla()
        _style_axes(self.ax, self._dark)
        self.ax.set_xlabel("Focus (mm)")
        self.ax.set_ylabel("FWHM (arcsec)")
        self.ax.set_title(title, fontsize=10)
        # Reserve room on the right for the legend (outside the axes) when needed
        self.fig.subplots_adjust(left=0.11, right=0.78 if with_legend else 0.97, bottom=0.17, top=0.9)

    def _legend(self):
        leg = self.ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8,
                             frameon=False, borderaxespad=0.0, handlelength=1.6)
        fg = "#dddddd" if self._dark else "#222222"
        for text in leg.get_texts():
            text.set_color(fg)

    def clear(self):
        self._setup_axes("Focus curve", with_legend=False)
        self.canvas.draw_idle()

    def plot_results(self, results: dict):
        """results: {filter_name_or_None: FocusResult}"""
        items = [(name, res) for name, res in results.items() if res and res.measurements]
        self._setup_axes("Focus curve", with_legend=bool(items))
        for i, (name, res) in enumerate(items):
            color = self.COLORS[i % len(self.COLORS)]
            xs = np.array(sorted(res.measurements))
            ys = np.array([res.measurements[x] for x in xs])
            label = name or "all"
            if res.success and res.fit_coefficients and any(res.fit_coefficients):
                a, b, c = res.fit_coefficients
                xf = np.linspace(xs.min(), xs.max(), 200)
                # One legend entry per filter: the fit line carries the label
                self.ax.plot(xs, ys, 'o', color=color, markersize=4, label='_nolegend_')
                self.ax.plot(xf, a * xf ** 2 + b * xf + c, '-', color=color, alpha=0.9,
                             label=f'{label}  {res.best_focus:.2f} mm')
                self.ax.axvline(res.best_focus, color=color, linestyle='--', alpha=0.4, linewidth=1)
            else:
                self.ax.plot(xs, ys, 'o-', color=color, markersize=4, label=f"{label}: no fit")
        if items:
            self._legend()
        self.canvas.draw_idle()

    def plot_partial(self, measurements: dict, label: str = ""):
        """Live update while a run is in progress."""
        self._setup_axes("Focus curve (running)", with_legend=bool(measurements))
        if measurements:
            xs = np.array(sorted(measurements))
            ys = np.array([measurements[x] for x in xs])
            self.ax.plot(xs, ys, 'o-', color=self.COLORS[0], markersize=4, label=label or "in progress")
            self._legend()
        self.canvas.draw_idle()
