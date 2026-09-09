"""
Pure numpy/scipy image analysis used by the live view (no Qt dependencies).

Ported from gui/panels/image_display.py so the Tk and Qt GUIs share the same
FWHM / photometry semantics.
"""

import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_LUT_CACHE = {}


def scale_to_8bit(frame: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    """Linear stretch to uint8. Uses a 65536-entry LUT for uint16 frames (fast)."""
    if vmax <= vmin:
        vmax = vmin + 1
    if frame.dtype == np.uint16:
        key = (float(vmin), float(vmax))
        lut = _LUT_CACHE.get(key)
        if lut is None:
            x = np.arange(65536, dtype=np.float32)
            lut = np.clip((x - vmin) / (vmax - vmin) * 255.0, 0, 255).astype(np.uint8)
            if len(_LUT_CACHE) > 16:
                _LUT_CACHE.clear()
            _LUT_CACHE[key] = lut
        return np.take(lut, frame)
    scaled = np.clip(frame.astype(np.float32), vmin, vmax)
    return ((scaled - vmin) / (vmax - vmin) * 255.0).astype(np.uint8)


def frame_stats(frame: np.ndarray, step: int = 8) -> Tuple[float, float, float, float]:
    """(mean, max, min, std) on a downsampled view to keep CPU low on 9 MP frames."""
    sample = frame[::step, ::step]
    return float(np.mean(sample)), float(np.max(sample)), float(np.min(sample)), float(np.std(sample))


def auto_scale_limits(frame: np.ndarray, step: int = 8, lo_pct: float = 0.5, hi_pct: float = 99.7):
    """Percentile-based display limits (robust against hot pixels and stars)."""
    sample = frame[::step, ::step]
    lo, hi = np.percentile(sample, [lo_pct, hi_pct])
    if hi <= lo:
        hi = lo + 1
    return float(lo), float(hi)


def measure_fwhm_gaussian(frame: np.ndarray, target: Tuple[int, int], box_size: int
                          ) -> Tuple[Optional[float], Optional[Tuple[float, float]]]:
    """
    2-D Gaussian fit around `target` in a `box_size` cutout.

    Returns:
        (fwhm_pixels, (dx, dy)) where dx/dy is the fitted centroid offset from
        the box centre in pixels, or (None, None) if the fit fails.
    """
    try:
        from scipy.optimize import curve_fit
        from scipy.ndimage import center_of_mass
    except ImportError:
        logger.warning("scipy not available for Gaussian fitting")
        return None, None

    cx, cy = target
    half_box = box_size // 2
    y1, y2 = cy - half_box, cy + half_box
    x1, x2 = cx - half_box, cx + half_box
    height, width = frame.shape[:2]
    if y1 < 0 or y2 > height or x1 < 0 or x2 > width or half_box < 2:
        return None, None

    cutout = frame[y1:y2, x1:x2].astype(np.float64)
    edge = np.concatenate([cutout[0, :], cutout[-1, :], cutout[1:-1, 0], cutout[1:-1, -1]])
    cutout = cutout - np.median(edge)

    try:
        com_y, com_x = center_of_mass(np.maximum(cutout, 0))
        if np.isnan(com_x) or np.isnan(com_y):
            com_x, com_y = half_box, half_box
    except Exception:
        com_x, com_y = half_box, half_box

    y_grid, x_grid = np.mgrid[0:cutout.shape[0], 0:cutout.shape[1]]

    def gaussian_2d(coords, amplitude, x0, y0, sigma_x, sigma_y, offset):
        x, y = coords
        return (offset + amplitude * np.exp(
            -((x - x0) ** 2 / (2 * sigma_x ** 2) + (y - y0) ** 2 / (2 * sigma_y ** 2))
        )).ravel()

    amplitude_guess = float(np.max(cutout) - np.min(cutout))
    if amplitude_guess <= 0:
        return None, None
    p0 = [amplitude_guess, com_x, com_y, 5.0, 5.0, 0.0]
    bounds = ([0, 0, 0, 1, 1, -np.inf],
              [np.inf, cutout.shape[1], cutout.shape[0], half_box, half_box, np.inf])
    try:
        popt, _ = curve_fit(gaussian_2d, (x_grid, y_grid), cutout.ravel(),
                            p0=p0, bounds=bounds, maxfev=1000)
    except Exception as e:
        logger.debug(f"Gaussian fit failed: {e}")
        return None, None

    _, x0, y0, sigma_x, sigma_y = popt[:5]
    fwhm_pixels = float(np.sqrt((2.355 * sigma_x) * (2.355 * sigma_y)))
    if fwhm_pixels < 1 or fwhm_pixels > box_size:
        return None, None
    return fwhm_pixels, (float(x0 - half_box), float(y0 - half_box))


def compute_flux(frame: np.ndarray, cx: int, cy: int, r_ap: int, r_in: int, r_out: int
                 ) -> Optional[float]:
    """Background-subtracted aperture flux with a median sky annulus."""
    height, width = frame.shape[:2]
    if cx - r_out < 0 or cx + r_out >= width or cy - r_out < 0 or cy + r_out >= height:
        return None
    x0, x1 = max(0, cx - r_out - 1), min(width, cx + r_out + 2)
    y0, y1 = max(0, cy - r_out - 1), min(height, cy + r_out + 2)
    sub = frame[y0:y1, x0:x1]
    yy, xx = np.ogrid[:sub.shape[0], :sub.shape[1]]
    dist_sq = (xx - (cx - x0)) ** 2 + (yy - (cy - y0)) ** 2
    annulus = sub[(dist_sq >= r_in ** 2) & (dist_sq <= r_out ** 2)]
    if annulus.size == 0:
        return None
    background = float(np.median(annulus))
    aperture = sub[dist_sq <= r_ap ** 2]
    return float(np.sum(aperture.astype(np.float64)) - background * aperture.size)


def roi_from_drag(p1: Tuple[int, int], p2: Tuple[int, int], offset: Tuple[int, int],
                  min_size: int = 16) -> Optional[Tuple[int, int, int, int]]:
    """Convert a drag rectangle (image coords) to a 4-pixel aligned sensor ROI."""
    left, right = min(p1[0], p2[0]), max(p1[0], p2[0])
    top, bottom = min(p1[1], p2[1]), max(p1[1], p2[1])
    if right - left < min_size or bottom - top < min_size:
        return None
    abs_left, abs_right = left + offset[0], right + offset[0]
    abs_top, abs_bottom = top + offset[1], bottom + offset[1]
    hpos = (abs_left // 4) * 4
    vpos = (abs_top // 4) * 4
    hsize = ((abs_right - hpos + 3) // 4) * 4
    vsize = ((abs_bottom - vpos + 3) // 4) * 4
    return hpos, vpos, hsize, vsize
