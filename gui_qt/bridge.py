"""
Bridges between non-Qt threads and the GUI thread.

- StateBridge: turns CerberusAPI status callbacks (which fire on whatever thread
  called the API) into a Qt signal delivered on the GUI thread, coalescing
  bursts so the panels are repainted at most every ~30 ms.
- QtLogHandler: a logging.Handler that forwards records to the GUI as a signal.
"""

import logging
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class StateBridge(QObject):
    """Delivers SystemState snapshots to the GUI thread, coalesced."""

    state_changed = pyqtSignal(object)   # SystemState (copy)
    _raw = pyqtSignal(object)

    def __init__(self, api, coalesce_ms: int = 30, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.api = api
        self._latest = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(coalesce_ms)
        self._timer.timeout.connect(self._flush)
        # _raw is emitted from arbitrary threads; the slot runs on the GUI thread
        self._raw.connect(self._on_raw)
        api.on_status_change(self._api_callback)

    def _api_callback(self, state):
        # Called from any thread (camera thread, worker pool, focus thread...)
        self._raw.emit(state)

    def _on_raw(self, state):
        self._latest = state
        if not self._timer.isActive():
            self._timer.start()

    def _flush(self):
        state = self._latest
        self._latest = None
        if state is not None:
            self.state_changed.emit(state)

    def detach(self):
        try:
            self.api.remove_status_callback(self._api_callback)
        except Exception:
            pass


class _LogEmitter(QObject):
    record_ready = pyqtSignal(str, int)   # formatted text, level number


class QtLogHandler(logging.Handler):
    """
    logging.Handler that forwards each record to the GUI thread as a Qt signal.

    Composition (not QObject inheritance) keeps logging.shutdown() at interpreter
    exit away from a possibly already-destroyed Qt object.
    """

    def __init__(self, level=logging.INFO):
        super().__init__(level)
        self._emitter = _LogEmitter()
        self.record_ready = self._emitter.record_ready
        self.setFormatter(logging.Formatter('%(asctime)s %(levelname)-7s %(name)s: %(message)s',
                                            datefmt='%H:%M:%S'))

    def emit(self, record: logging.LogRecord):
        emitter = self._emitter
        if emitter is None:
            return
        try:
            emitter.record_ready.emit(self.format(record), record.levelno)
        except RuntimeError:
            self._emitter = None      # Qt side is gone; stop forwarding
        except Exception:
            pass

    def close(self):
        self._emitter = None
        super().close()
