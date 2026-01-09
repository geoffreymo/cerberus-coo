"""
Focus analysis module using SExtractor for FWHM measurement.

Refactored from focus_loop.py for reusable library use.
"""

import os
import subprocess
import tempfile
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from astropy.table import Table
import logging

# Plate scale for Cerberus (arcsec/pixel)
PLATE_SCALE = 0.051

# Saturation threshold
SATURATION_LEVEL = 60000

# Minimum FWHM to avoid cosmic rays (pixels)
MIN_FWHM_PIXELS = 5


@dataclass
class FocusResult:
    """Result of focus analysis."""
    best_focus: float
    best_fwhm_arcsec: float
    measurements: Dict[float, float]  # position -> FWHM in arcsec
    fit_coefficients: Tuple[float, float, float]  # a, b, c for ax^2 + bx + c
    success: bool
    error_message: Optional[str] = None


class FocusAnalyzer:
    """
    Analyze FWHM from images using SExtractor.

    Usage:
        analyzer = FocusAnalyzer()
        fwhm = analyzer.measure_fwhm("/path/to/image.fits")

        # Or analyze full focus run:
        result = analyzer.analyze_focus_sequence({
            35.0: "/path/to/focus_35.0.fits",
            37.5: "/path/to/focus_37.5.fits",
            40.0: "/path/to/focus_40.0.fits",
        })
    """

    def __init__(self, config_dir: Optional[Path] = None, plate_scale: float = PLATE_SCALE):
        """
        Initialize FocusAnalyzer.

        Args:
            config_dir: Directory containing focus.sex, focus.param, default.conv
                       If None, uses package default config
            plate_scale: Plate scale in arcsec/pixel
        """
        self.logger = logging.getLogger(__name__)
        self.plate_scale = plate_scale

        if config_dir is None:
            # Use package config directory
            self.config_dir = Path(__file__).parent / "config"
        else:
            self.config_dir = Path(config_dir)

        self._validate_config()

    def _validate_config(self):
        """Verify SExtractor config files exist."""
        required = ['focus.sex', 'focus.param', 'default.conv']
        for f in required:
            if not (self.config_dir / f).exists():
                raise FileNotFoundError(f"Missing SExtractor config: {self.config_dir / f}")

    def run_sextractor(self, image_path: str, catalog_path: Optional[str] = None) -> Path:
        """
        Run SExtractor on an image.

        Args:
            image_path: Path to FITS image
            catalog_path: Output catalog path (auto-generated if None)

        Returns:
            Path to output catalog
        """
        image_path = Path(image_path)

        if catalog_path is None:
            catalog_path = image_path.with_suffix('.cat')

        cmd = [
            "sex",
            "-c", str(self.config_dir / "focus.sex"),
            str(image_path),
            "-CATALOG_NAME", str(catalog_path),
            "-PARAMETERS_NAME", str(self.config_dir / "focus.param"),
            "-FILTER_NAME", str(self.config_dir / "default.conv"),
        ]

        self.logger.debug(f"Running: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"SExtractor failed: {result.stderr}")

        return Path(catalog_path)

    def get_fwhm_from_catalog(self, catalog_path: str,
                               saturation: float = SATURATION_LEVEL,
                               min_fwhm: float = MIN_FWHM_PIXELS) -> float:
        """
        Extract FWHM from SExtractor catalog.

        Uses FWHM of the highest SNR non-saturated source with FWHM > min_fwhm.

        Args:
            catalog_path: Path to SExtractor catalog
            saturation: Saturation threshold in ADU
            min_fwhm: Minimum FWHM in pixels (filters cosmic rays)

        Returns:
            float: FWHM in pixels

        Raises:
            ValueError: If no valid sources found
        """
        catalog = Table.read(catalog_path, format='ascii')

        # Filter saturated sources
        catalog = catalog[catalog['FLUX_MAX'] < saturation]

        # Filter cosmic rays (tiny FWHM)
        catalog = catalog[catalog['FWHM_IMAGE'] > min_fwhm]

        if len(catalog) == 0:
            raise ValueError("No valid sources found in catalog")

        # Sort by SNR descending and take the top source
        sorted_catalog = catalog[np.argsort(catalog['SNR_WIN'])[::-1]]
        fwhm_pixels = sorted_catalog['FWHM_IMAGE'][0]

        return float(fwhm_pixels)

    def measure_fwhm(self, image_path: str, cleanup: bool = True) -> float:
        """
        Measure FWHM from a single image.

        Args:
            image_path: Path to FITS image
            cleanup: Remove temporary catalog file

        Returns:
            float: FWHM in arcseconds
        """
        with tempfile.NamedTemporaryFile(suffix='.cat', delete=False) as tmp:
            catalog_path = tmp.name

        try:
            self.run_sextractor(image_path, catalog_path)
            fwhm_pixels = self.get_fwhm_from_catalog(catalog_path)
            fwhm_arcsec = fwhm_pixels * self.plate_scale

            self.logger.info(f"{image_path}: FWHM = {fwhm_arcsec:.3f} arcsec ({fwhm_pixels:.1f} px)")
            return fwhm_arcsec

        finally:
            if cleanup and os.path.exists(catalog_path):
                os.remove(catalog_path)

    def fit_parabola(self, focus_positions: List[float],
                     fwhm_values: List[float]) -> Tuple[float, float, Tuple[float, float, float]]:
        """
        Fit parabola to focus measurements.

        Args:
            focus_positions: Array of focus positions
            fwhm_values: Array of FWHM values

        Returns:
            Tuple of (best_focus, best_fwhm, coefficients)

        Raises:
            ValueError: If parabola opens downward (invalid focus curve)
        """
        x = np.array(focus_positions, dtype=float)
        y = np.array(fwhm_values, dtype=float)

        # Fit y = ax^2 + bx + c
        coefficients = np.polyfit(x, y, 2)
        a, b, c = coefficients

        # Best focus is vertex of parabola
        if a <= 0:
            raise ValueError("Parabola opens downward - invalid focus curve (check data quality)")

        best_focus = -b / (2 * a)
        best_fwhm = np.polyval(coefficients, best_focus)

        return float(best_focus), float(best_fwhm), (float(a), float(b), float(c))

    def analyze_focus_sequence(self, images: Dict[float, str],
                                cleanup_catalogs: bool = True) -> FocusResult:
        """
        Analyze a complete focus sequence.

        Args:
            images: Dict mapping focus_position -> image_path
            cleanup_catalogs: Remove temporary catalog files

        Returns:
            FocusResult with best focus, measurements, and fit
        """
        measurements = {}

        for position, image_path in sorted(images.items()):
            try:
                fwhm = self.measure_fwhm(image_path, cleanup=cleanup_catalogs)
                measurements[position] = fwhm
            except Exception as e:
                self.logger.warning(f"Failed to measure FWHM at {position}: {e}")
                continue

        if len(measurements) < 3:
            return FocusResult(
                best_focus=0.0,
                best_fwhm_arcsec=0.0,
                measurements=measurements,
                fit_coefficients=(0.0, 0.0, 0.0),
                success=False,
                error_message=f"Need at least 3 measurements, got {len(measurements)}"
            )

        try:
            positions = list(measurements.keys())
            fwhms = list(measurements.values())
            best_focus, best_fwhm, coeffs = self.fit_parabola(positions, fwhms)

            self.logger.info(f"Best focus: {best_focus:.2f}, Best FWHM: {best_fwhm:.3f} arcsec")

            return FocusResult(
                best_focus=best_focus,
                best_fwhm_arcsec=best_fwhm,
                measurements=measurements,
                fit_coefficients=coeffs,
                success=True
            )

        except Exception as e:
            return FocusResult(
                best_focus=0.0,
                best_fwhm_arcsec=0.0,
                measurements=measurements,
                fit_coefficients=(0.0, 0.0, 0.0),
                success=False,
                error_message=str(e)
            )

    def _get_focus_from_header(self, fits_path: str) -> Optional[float]:
        """
        Read focus position from FITS header.

        Looks for TELFOCUS or FOCUS keywords (TELFOCUS preferred).

        Args:
            fits_path: Path to FITS file

        Returns:
            Focus position in mm, or None if not found
        """
        from astropy.io import fits

        try:
            with fits.open(fits_path) as hdul:
                # Check primary header first
                header = hdul[0].header
                if 'TELFOCUS' in header:
                    return float(header['TELFOCUS'])
                if 'FOCUS' in header:
                    return float(header['FOCUS'])

                # Check image extension if present
                if len(hdul) > 1 and hasattr(hdul[1], 'header'):
                    header = hdul[1].header
                    if 'TELFOCUS' in header:
                        return float(header['TELFOCUS'])
                    if 'FOCUS' in header:
                        return float(header['FOCUS'])

        except Exception as e:
            self.logger.warning(f"Could not read focus from {fits_path}: {e}")

        return None

    def analyze_directory(self, directory: str,
                          pattern: str = "*focus*.fits",
                          position_parser: Optional[callable] = None,
                          use_headers: bool = True) -> FocusResult:
        """
        Analyze focus images from a directory.

        Args:
            directory: Directory containing focus images
            pattern: Glob pattern to match focus images
            position_parser: Function to extract focus position from filename.
                           If None and use_headers=False, expects 'focus_[POS]_*.fits'
            use_headers: If True (default), read focus position from FITS headers
                        instead of parsing filenames. More robust.

        Returns:
            FocusResult
        """
        directory = Path(directory)
        images = list(directory.glob(pattern))

        if len(images) == 0:
            return FocusResult(
                best_focus=0.0,
                best_fwhm_arcsec=0.0,
                measurements={},
                fit_coefficients=(0.0, 0.0, 0.0),
                success=False,
                error_message=f"No images found matching {pattern} in {directory}"
            )

        # Build images dict
        image_dict = {}
        for img_path in sorted(images):
            try:
                if use_headers:
                    # Read focus position from FITS header (more robust)
                    position = self._get_focus_from_header(str(img_path))
                    if position is None:
                        self.logger.warning(f"No focus in header for {img_path}, trying filename")
                        # Fall back to filename parsing
                        position = float(img_path.stem.split('_')[2])  # timestamp_focus_POS_filter.fits
                elif position_parser is not None:
                    position = position_parser(img_path)
                else:
                    # Default filename parser: expects '*_focus_[POS]_*.fits'
                    position = float(img_path.stem.split('_')[2])

                image_dict[position] = str(img_path)
            except (IndexError, ValueError) as e:
                self.logger.warning(f"Could not get position for {img_path}: {e}")
                continue

        self.logger.info(f"Found {len(image_dict)} focus images in {directory}")

        return self.analyze_focus_sequence(image_dict)

    def plot_focus_curve(self, result: FocusResult, output_path: Optional[str] = None) -> Optional[str]:
        """
        Plot the focus curve with parabolic fit.

        Args:
            result: FocusResult from analyze_focus_sequence
            output_path: Path to save plot (None to auto-generate)

        Returns:
            Path to saved plot, or None if not saved
        """
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend for saving plots
        import matplotlib.pyplot as plt

        if not result.measurements:
            self.logger.warning("No measurements to plot")
            return None

        x = np.array(list(result.measurements.keys()))
        y = np.array(list(result.measurements.values()))

        plt.figure(figsize=(8, 6))
        plt.scatter(x, y, color='red', s=60, zorder=5, label='Measured FWHM')

        if result.success:
            # Plot fit curve
            xx = np.linspace(x.min(), x.max(), 400)
            yy = np.polyval(result.fit_coefficients, xx)
            plt.plot(xx, yy, 'b-', linewidth=2, label='Parabolic Fit')

            # Mark best focus
            plt.axvline(result.best_focus, color='green', linestyle='--',
                       label=f'Best Focus = {result.best_focus:.2f} mm, FWHM = {result.best_fwhm_arcsec:.3f}"')
            plt.scatter([result.best_focus], [result.best_fwhm_arcsec],
                       color='green', s=100, zorder=6, marker='*')

        plt.xlabel("Focus Position (mm)", fontsize=12)
        plt.ylabel("FWHM (arcsec)", fontsize=12)
        plt.title("FWHM vs Focus Position", fontsize=14)
        plt.legend(loc='best')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()

        if output_path:
            plt.savefig(output_path, dpi=150)
            self.logger.info(f"Saved plot to {output_path}")

        plt.close()

        return output_path
