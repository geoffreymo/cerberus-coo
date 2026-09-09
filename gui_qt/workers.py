"""
Background task helpers.

All potentially blocking API calls (hardware connects, streaming start/stop,
focus moves, filter moves, status polling) are executed via `run_async` so the
GUI thread never blocks. Completion/error callbacks are delivered on the GUI
thread through Qt queued signals.
"""

import logging
import traceback
from typing import Callable, Optional, Set

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)


class TaskSignals(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    done = pyqtSignal()


class Task(QRunnable):
    """QRunnable wrapper that reports the result back to the GUI thread."""

    def __init__(self, fn: Callable, *args, name: str = "", **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.name = name or getattr(fn, '__name__', 'task')
        self.signals = TaskSignals()
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # noqa: BLE001 - report everything to the GUI
            logger.error(f"Background task '{self.name}' failed: {e}\n{traceback.format_exc()}")
            self.signals.error.emit(f"{type(e).__name__}: {e}")
        else:
            self.signals.finished.emit(result)
        finally:
            self.signals.done.emit()


_live_tasks: Set[Task] = set()


def run_async(fn: Callable, *args, on_done: Optional[Callable] = None,
              on_error: Optional[Callable] = None, on_finally: Optional[Callable] = None,
              name: str = "", **kwargs) -> Task:
    """
    Run `fn(*args, **kwargs)` on the global thread pool.

    Args:
        on_done: called on the GUI thread with the return value
        on_error: called on the GUI thread with an error string
        on_finally: called on the GUI thread after either of the above
    """
    task = Task(fn, *args, name=name, **kwargs)
    _live_tasks.add(task)

    def _cleanup():
        _live_tasks.discard(task)
        if on_finally:
            on_finally()

    if on_done:
        task.signals.finished.connect(on_done)
    if on_error:
        task.signals.error.connect(on_error)
    task.signals.done.connect(_cleanup)
    QThreadPool.globalInstance().start(task)
    return task


def wait_for_tasks(timeout_ms: int = 5000) -> bool:
    """Block (rarely needed - shutdown only) until all pool tasks finish."""
    return QThreadPool.globalInstance().waitForDone(timeout_ms)
