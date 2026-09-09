"""
Synthetic star-field renderer for the simulated camera.

Renders a fixed catalogue of Gaussian stars onto a background with realistic
noise. Star size follows the simulated telescope focus, the whole field shifts
with tracking drift and telescope offsets, and one star near the sensor centre
is variable so that the photometry tools have something to measure.
"""

import math
import threading
import time
from typing import Optional, Tuple

import numpy as np

from .world import SimWorld


class SkyModel:
    """Deterministic star field for one simulated camera."""

    def __init__(self, world: SimWorld, width: int, height: int, seed: int = 0,
                 plate_scale: float = 0.051, n_stars: int = 70,
                 bias_adu: float = 200.0, sky_adu_per_s: float = 30.0,
                 read_noise_adu: float = 1.6):
        self.world = world
        self.width = width
        self.height = height
        self.plate_scale = plate_scale
        self.bias = bias_adu
        self.sky_rate = sky_adu_per_s
        self.read_noise = read_noise_adu
        self._t0 = time.time()
        self._lock = threading.Lock()

        rng = np.random.default_rng(1000 + seed)
        # Random field, keep a margin so stars don't sit on the edge
        n = n_stars
        xs = rng.uniform(60, width - 60, n)
        ys = rng.uniform(60, height - 60, n)
        # Log-uniform fluxes (ADU/s at peak-normalised total flux)
        flux = 10 ** rng.uniform(3.3, 5.6, n)

        # Guarantee a bright target + comparison pair near the centre
        cx, cy = width / 2, height / 2
        xs[0], ys[0], flux[0] = cx - 180, cy - 40, 2.5e5     # variable target
        xs[1], ys[1], flux[1] = cx + 220, cy + 90, 3.0e5     # comparison
        xs[2], ys[2], flux[2] = cx + 40, cy - 260, 6.0e5     # bright guide star

        self.star_x = xs
        self.star_y = ys
        self.star_flux = flux
        self.variable_index = 0
        self.variable_amplitude = 0.12
        self.variable_period_s = 90.0

        # Hot pixels (shown when defect correction is OFF)
        self.hot_x = rng.integers(0, width, 40)
        self.hot_y = rng.integers(0, height, 40)

        # Unit-variance noise bank (float32) - sliced/flipped per frame
        self._bank = [rng.standard_normal((height, width), dtype=np.float32) for _ in range(3)]
        self._bank_rng = np.random.default_rng(seed + 7)

    # ------------------------------------------------------------------

    def _fwhm_pixels(self) -> float:
        return max(2.0, self.world.fwhm_arcsec() / self.plate_scale)

    def _shift_pixels(self) -> Tuple[float, float]:
        dra, ddec = self.world.field_shift_arcsec()
        # +RA offset moves the field by +x pixels, +Dec by +y (matches
        # guiding.x_to_ra_sign = -1 / y_to_dec_sign = -1 in config.json)
        return dra / self.plate_scale, ddec / self.plate_scale

    def _noise_view(self, y0: int, y1: int, x0: int, x1: int) -> np.ndarray:
        k = int(self._bank_rng.integers(0, len(self._bank)))
        v = self._bank[k][y0:y1, x0:x1]
        flip = int(self._bank_rng.integers(0, 4))
        if flip & 1:
            v = v[::-1, :]
        if flip & 2:
            v = v[:, ::-1]
        return v

    def render(self, exposure_s: float, hpos: int = 0, vpos: int = 0,
               hsize: Optional[int] = None, vsize: Optional[int] = None,
               binning: int = 1, defect_correct: bool = True,
               saturation: int = 65535) -> np.ndarray:
        """Render one frame (uint16) for the given subarray/binning."""
        hsize = self.width if hsize is None else hsize
        vsize = self.height if vsize is None else vsize
        x0 = max(0, min(hpos, self.width - 4))
        y0 = max(0, min(vpos, self.height - 4))
        x1 = max(x0 + 4, min(x0 + hsize, self.width))
        y1 = max(y0 + 4, min(y0 + vsize, self.height))
        h, w = y1 - y0, x1 - x0
        exposure_s = max(exposure_s, 1e-5)

        with self._lock:
            sky = self.sky_rate * exposure_s
            sigma_noise = math.sqrt(self.read_noise ** 2 + sky)
            img = self._noise_view(y0, y1, x0, x1) * np.float32(sigma_noise)
            img += np.float32(self.bias + sky)

            # Stars
            fwhm = self._fwhm_pixels()
            sig = fwhm / 2.3548
            half = int(math.ceil(3.5 * sig))
            dx, dy = self._shift_pixels()
            t = time.time() - self._t0
            var_scale = 1.0 + self.variable_amplitude * math.sin(2 * math.pi * t / self.variable_period_s)
            two_sig2 = 2.0 * sig * sig
            peak_norm = 1.0 / (2.0 * math.pi * sig * sig)

            for i in range(len(self.star_x)):
                sx = self.star_x[i] + dx - x0
                sy = self.star_y[i] + dy - y0
                if sx < -half or sx > w + half or sy < -half or sy > h + half:
                    continue
                flux = self.star_flux[i] * exposure_s
                if i == self.variable_index:
                    flux *= var_scale
                ix, iy = int(round(sx)), int(round(sy))
                xa, xb = max(0, ix - half), min(w, ix + half + 1)
                ya, yb = max(0, iy - half), min(h, iy + half + 1)
                if xb <= xa or yb <= ya:
                    continue
                gx = np.exp(-((np.arange(xa, xb) - sx) ** 2) / two_sig2).astype(np.float32)
                gy = np.exp(-((np.arange(ya, yb) - sy) ** 2) / two_sig2).astype(np.float32)
                stamp = np.outer(gy, gx) * np.float32(flux * peak_norm)
                # Photon noise on the star itself
                stamp += np.sqrt(np.maximum(stamp, 0)) * np.random.standard_normal(stamp.shape).astype(np.float32)
                img[ya:yb, xa:xb] += stamp

            if not defect_correct:
                for hx, hy in zip(self.hot_x, self.hot_y):
                    if x0 <= hx < x1 and y0 <= hy < y1:
                        img[hy - y0, hx - x0] = saturation

        if binning > 1:
            bh, bw = (h // binning) * binning, (w // binning) * binning
            img = img[:bh, :bw].reshape(bh // binning, binning, bw // binning, binning).sum(axis=(1, 3))

        np.clip(img, 0, saturation, out=img)
        return img.astype(np.uint16)
