"""
Shared simulated "world" state: telescope focus/pointing and sky drift.

Both the simulated telescope and the simulated cameras read from the same
SimWorld so that focus moves change star FWHM and offset moves shift the
star field (which is what closes the guiding loop).
"""

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


# Palomar
SITE_LAT_DEG = 33.3563
SITE_LON_DEG = -116.8650


def _gmst_hours(unix_time: float) -> float:
    """Greenwich mean sidereal time in hours (approximate, good to ~1 s)."""
    jd = unix_time / 86400.0 + 2440587.5
    d = jd - 2451545.0
    gmst = 18.697374558 + 24.06570982441908 * d
    return gmst % 24.0


def _hms(hours: float, precision: int = 2) -> str:
    sign = '-' if hours < 0 else ''
    hours = abs(hours) % 24
    h = int(hours)
    m = int((hours - h) * 60)
    s = ((hours - h) * 60 - m) * 60
    return f"{sign}{h:02d}:{m:02d}:{s:0{3 + precision}.{precision}f}"


def _dms(deg: float, precision: int = 1) -> str:
    sign = '-' if deg < 0 else '+'
    deg = abs(deg)
    d = int(deg)
    m = int((deg - d) * 60)
    s = ((deg - d) * 60 - m) * 60
    return f"{sign}{d:02d}:{m:02d}:{s:0{3 + precision}.{precision}f}"


@dataclass
class SimWorld:
    """Mutable simulated environment shared by all simulated devices."""

    # Focus (mm)
    optimal_focus_mm: float = 26.5
    focus_mm: float = 26.0
    focus_target_mm: float = 26.0
    focus_speed_mm_s: float = 3.0
    focus_min_mm: float = 1.0
    focus_max_mm: float = 74.0

    # Pointing
    ra_hours: float = 12.5825          # 12:34:57
    dec_deg: float = 45.5
    offset_ra_arcsec: float = 0.0
    offset_dec_arcsec: float = 0.0
    rate_ra_arcsec_hr: float = 0.0
    rate_dec_arcsec_hr: float = 0.0
    cass_ring_angle: float = 0.0
    tube_length_mm: float = 1234.5
    telescope_id: int = 200

    # Uncorrected tracking drift of the star field (arcsec/s) -- guiding fixes this
    drift_ra_arcsec_s: float = 0.02
    drift_dec_arcsec_s: float = -0.012

    # Seeing (arcsec) -- best achievable FWHM
    seeing_arcsec: float = 0.9
    # Defocus slope: FWHM growth (arcsec per mm of defocus)
    defocus_arcsec_per_mm: float = 0.55

    # Current filter (set by the simulated filter wheel)
    current_filter: Optional[str] = None

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_tick: float = field(default_factory=time.time, repr=False)
    _t0: float = field(default_factory=time.time, repr=False)

    # --- focus -------------------------------------------------------------

    def _tick(self):
        """Advance focus motion (called lazily under the lock)."""
        now = time.time()
        dt = now - self._last_tick
        self._last_tick = now
        if self.focus_mm != self.focus_target_mm:
            step = self.focus_speed_mm_s * dt
            delta = self.focus_target_mm - self.focus_mm
            if abs(delta) <= step:
                self.focus_mm = self.focus_target_mm
            else:
                self.focus_mm += math.copysign(step, delta)

    def get_focus(self) -> float:
        with self._lock:
            self._tick()
            return self.focus_mm

    def set_focus_target(self, mm: float) -> bool:
        if not (self.focus_min_mm <= mm <= self.focus_max_mm):
            return False
        with self._lock:
            self._tick()
            self.focus_target_mm = float(mm)
        return True

    def focus_moving(self) -> bool:
        with self._lock:
            self._tick()
            return self.focus_mm != self.focus_target_mm

    def fwhm_arcsec(self) -> float:
        """Current PSF FWHM in arcsec for the current focus."""
        defocus = self.get_focus() - self.optimal_focus_mm
        blur = self.defocus_arcsec_per_mm * defocus
        return math.sqrt(self.seeing_arcsec ** 2 + blur ** 2)

    # --- pointing ----------------------------------------------------------

    def apply_offset(self, ra_arcsec: float, dec_arcsec: float):
        with self._lock:
            self.offset_ra_arcsec += ra_arcsec
            self.offset_dec_arcsec += dec_arcsec

    def field_shift_arcsec(self) -> tuple:
        """Total displacement of the star field (drift + telescope offsets)."""
        t = time.time() - self._t0
        with self._lock:
            return (self.drift_ra_arcsec_s * t + self.offset_ra_arcsec,
                    self.drift_dec_arcsec_s * t + self.offset_dec_arcsec)

    def lst_hours(self) -> float:
        return (_gmst_hours(time.time()) + SITE_LON_DEG / 15.0) % 24.0

    def ha_hours(self) -> float:
        ha = self.lst_hours() - self.ra_hours
        if ha > 12:
            ha -= 24
        if ha < -12:
            ha += 24
        return ha

    def airmass(self) -> float:
        lat = math.radians(SITE_LAT_DEG)
        dec = math.radians(self.dec_deg)
        ha = math.radians(self.ha_hours() * 15.0)
        sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)
        sin_alt = max(sin_alt, 0.05)
        return round(1.0 / sin_alt, 3)

    def position_strings(self) -> dict:
        now = time.gmtime()
        return {
            'utc_day': now.tm_yday,
            'utc_time': time.strftime('%H:%M:%S', now) + f".{int((time.time() % 1) * 10)}",
            'lst': _hms(self.lst_hours(), 1),
            'ra': _hms(self.ra_hours, 2),
            'dec': _dms(self.dec_deg, 1),
            'ha': _hms(self.ha_hours(), 1),
            'airmass': self.airmass(),
        }
