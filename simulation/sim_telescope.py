"""Simulated telescope controller (drop-in for hardware.telescope.TelescopeController)."""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from .world import SimWorld

logger = logging.getLogger(__name__)


@dataclass
class SimTelescopePosition:
    utc_day: int
    utc_time: str
    lst: str
    ra: str
    dec: str
    ha: str
    airmass: float


@dataclass
class SimTelescopeStatus:
    utc_day: int
    utc_time: str
    telescope_id: int
    focus_mm: float
    tube_length_mm: float
    offset_ra_arcsec: float
    offset_dec_arcsec: float
    rate_ra_arcsec_hr: float
    rate_dec_arcsec_hr: float
    cass_ring_angle: float


@dataclass
class FocusStatus:
    position_mm: float
    tube_length_mm: float


class SimTelescopeController:
    """Simulated P200 TCS."""

    def __init__(self, world: SimWorld, connect_delay_s: float = 0.4, fail_connect: bool = False):
        self.world = world
        self._connect_delay = connect_delay_s
        self._fail_connect = fail_connect
        self._is_connected = False
        self._focus_min = world.focus_min_mm
        self._focus_max = world.focus_max_mm

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def connect(self) -> bool:
        if self._is_connected:
            return True
        logger.info("[SIM] Connecting to simulated TCS")
        time.sleep(self._connect_delay)
        if self._fail_connect:
            logger.error("[SIM] Simulated TCS connection failure")
            return False
        self._is_connected = True
        return True

    def disconnect(self):
        self._is_connected = False
        logger.info("[SIM] Disconnected from simulated TCS")

    # --- focus ---

    def set_focus(self, position_mm: float) -> bool:
        if not self._is_connected:
            logger.error("[SIM] Not connected to TCS")
            return False
        if not (self._focus_min <= position_mm <= self._focus_max):
            logger.error(f"[SIM] Focus {position_mm} out of range")
            return False
        logger.info(f"[SIM] Setting focus to {position_mm:.2f} mm")
        return self.world.set_focus_target(position_mm)

    def offset_focus(self, offset_mm: float) -> bool:
        if not self._is_connected:
            return False
        target = self.world.focus_target_mm + offset_mm
        return self.set_focus(target)

    def get_focus(self) -> Optional[float]:
        if not self._is_connected:
            return None
        return round(self.world.get_focus(), 3)

    def get_focus_status(self) -> Optional[FocusStatus]:
        if not self._is_connected:
            return None
        return FocusStatus(self.world.get_focus(), self.world.tube_length_mm)

    def wait_for_focus(self, target_mm: float, tolerance_mm: float = 1.0,
                       timeout_sec: float = 60.0, poll_interval: float = 0.5) -> bool:
        if not self._is_connected:
            return False
        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            if abs(self.world.get_focus() - target_mm) <= tolerance_mm and not self.world.focus_moving():
                return True
            time.sleep(min(poll_interval, 0.1))
        return False

    # --- position ---

    def get_position(self) -> Optional[SimTelescopePosition]:
        if not self._is_connected:
            return None
        return SimTelescopePosition(**self.world.position_strings())

    def get_status(self) -> Optional[SimTelescopeStatus]:
        if not self._is_connected:
            return None
        p = self.world.position_strings()
        return SimTelescopeStatus(
            utc_day=p['utc_day'], utc_time=p['utc_time'],
            telescope_id=self.world.telescope_id,
            focus_mm=round(self.world.get_focus(), 3),
            tube_length_mm=self.world.tube_length_mm,
            offset_ra_arcsec=round(self.world.offset_ra_arcsec, 3),
            offset_dec_arcsec=round(self.world.offset_dec_arcsec, 3),
            rate_ra_arcsec_hr=self.world.rate_ra_arcsec_hr,
            rate_dec_arcsec_hr=self.world.rate_dec_arcsec_hr,
            cass_ring_angle=self.world.cass_ring_angle,
        )

    # --- moves ---

    def move_offset(self, ra_arcsec: float, dec_arcsec: float) -> bool:
        if not self._is_connected:
            logger.error("[SIM] Not connected to TCS")
            return False
        logger.info(f"[SIM] Moving offset: RA {ra_arcsec:+.2f}\", Dec {dec_arcsec:+.2f}\"")
        self.world.apply_offset(ra_arcsec, dec_arcsec)
        time.sleep(0.05)
        return True

    def move_north(self, arcsec: float) -> bool:
        return self.move_offset(0.0, +arcsec)

    def move_south(self, arcsec: float) -> bool:
        return self.move_offset(0.0, -arcsec)

    def move_east(self, arcsec: float) -> bool:
        return self.move_offset(+arcsec, 0.0)

    def move_west(self, arcsec: float) -> bool:
        return self.move_offset(-arcsec, 0.0)

    def __enter__(self):
        if not self.connect():
            raise ConnectionError("Failed to connect to simulated TCS")
        return self

    def __exit__(self, *exc):
        self.disconnect()
        return False
