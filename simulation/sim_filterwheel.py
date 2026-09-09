"""Simulated ZWO EFW filter wheel (drop-in for filterwheel.FilterWheel)."""

import logging
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SimFilterWheel:
    """Simulates a filter wheel that takes ~0.4 s per slot to move."""

    def __init__(self, filters: Optional[Dict] = None, world=None, move_time_per_slot: float = 0.4):
        if filters:
            self.filters = {int(k): v for k, v in filters.items()}
        else:
            self.filters = {0: 'clear', 1: 'u', 2: 'g', 3: 'r', 4: 'i', 5: 'z', 6: 'Ha', 7: 'OIII'}
        self.num_slots = max(8, len(self.filters))
        self._pos = 0
        self._target = 0
        self._move_until = 0.0
        self._move_time = move_time_per_slot
        self._lock = threading.Lock()
        self._world = world
        self._closed = False
        if world is not None:
            world.current_filter = self.filters.get(0)
        time.sleep(0.2)

    @property
    def position(self) -> int:
        with self._lock:
            if time.time() < self._move_until:
                return -1
            self._pos = self._target
            return self._pos

    @position.setter
    def position(self, pos: int):
        if pos < 0 or pos >= self.num_slots:
            raise ValueError(f"Position must be 0-{self.num_slots - 1}")
        with self._lock:
            dist = min(abs(pos - self._pos), self.num_slots - abs(pos - self._pos))
            self._target = pos
            self._move_until = time.time() + dist * self._move_time
        if self._world is not None:
            self._world.current_filter = self.filters.get(pos)
        logger.info(f"[SIM] Filter wheel moving to slot {pos} ({self.filters.get(pos)})")

    @property
    def filter(self) -> str:
        pos = self.position
        if pos == -1:
            return "Moving..."
        return self.filters.get(pos, f"Position {pos}")

    @filter.setter
    def filter(self, name: str):
        for pos, fname in self.filters.items():
            if fname.lower() == name.lower():
                self.position = pos
                return
        raise ValueError(f"Unknown filter: {name}. Available: {list(self.filters.values())}")

    def wait_for_move(self, timeout: float = 30.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if self.position >= 0:
                return True
            time.sleep(0.05)
        raise TimeoutError("Filter wheel move timed out")

    def goto(self, filter_or_position):
        if isinstance(filter_or_position, int):
            self.position = filter_or_position
        else:
            self.filter = filter_or_position
        self.wait_for_move()

    def close(self):
        self._closed = True

    def __repr__(self):
        return f"SimFilterWheel(position={self.position}, filter='{self.filter}', slots={self.num_slots})"
