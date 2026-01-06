#!/usr/bin/env python3
"""
Test script for focus loop with simulated data.

Creates mock telescope/camera, generates synthetic star images with
varying FWHM, and runs the full focus loop to verify functionality.
"""

import os
import sys
import logging
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# =============================================================================
# Simulated Star Image Generation
# =============================================================================

def make_gaussian_star(
    size: tuple = (512, 512),
    center: tuple = None,
    fwhm_pixels: float = 10.0,
    peak_flux: float = 50000,
    background: float = 200,
    noise_std: float = 20
) -> np.ndarray:
    """
    Generate a 2D Gaussian star image.

    Args:
        size: Image dimensions (height, width)
        center: Star center (y, x), defaults to image center
        fwhm_pixels: Full Width at Half Maximum in pixels
        peak_flux: Peak flux of star above background
        background: Background level in ADU
        noise_std: Standard deviation of Gaussian noise

    Returns:
        2D numpy array (uint16)
    """
    height, width = size
    if center is None:
        center = (height // 2, width // 2)

    y, x = np.ogrid[:height, :width]
    cy, cx = center

    # Convert FWHM to sigma: FWHM = 2.355 * sigma
    sigma = fwhm_pixels / 2.355

    # 2D Gaussian
    r2 = (x - cx)**2 + (y - cy)**2
    star = peak_flux * np.exp(-r2 / (2 * sigma**2))

    # Add background and noise
    image = background + star + np.random.normal(0, noise_std, size)

    # Clip to valid range and convert to uint16
    image = np.clip(image, 0, 65535).astype(np.uint16)

    return image


def make_multi_star_image(
    size: tuple = (512, 512),
    n_stars: int = 5,
    fwhm_pixels: float = 10.0,
    background: float = 200,
    noise_std: float = 20,
    seed: int = None
) -> np.ndarray:
    """
    Generate image with multiple stars at random positions.

    Args:
        size: Image dimensions (height, width)
        n_stars: Number of stars to add
        fwhm_pixels: FWHM for all stars (same seeing)
        background: Background level
        noise_std: Noise standard deviation
        seed: Random seed for reproducibility

    Returns:
        2D numpy array (uint16)
    """
    if seed is not None:
        np.random.seed(seed)

    height, width = size

    # Start with background + noise
    image = background + np.random.normal(0, noise_std, size)

    # Add stars at random positions with varying brightness
    sigma = fwhm_pixels / 2.355
    y, x = np.ogrid[:height, :width]

    for i in range(n_stars):
        # Random position (avoid edges)
        margin = int(5 * fwhm_pixels)
        cy = np.random.randint(margin, height - margin)
        cx = np.random.randint(margin, width - margin)

        # Random peak flux (varying star brightness)
        peak_flux = np.random.uniform(20000, 55000)

        # Add Gaussian star
        r2 = (x - cx)**2 + (y - cy)**2
        star = peak_flux * np.exp(-r2 / (2 * sigma**2))
        image += star

    # Clip and convert
    image = np.clip(image, 0, 65535).astype(np.uint16)

    return image


def focus_to_fwhm(focus_position: float,
                  optimal_focus: float = 37.5,
                  min_fwhm: float = 3.0,
                  defocus_coeff: float = 0.5) -> float:
    """
    Convert focus position to expected FWHM (parabolic relationship).

    FWHM = min_fwhm + defocus_coeff * (focus - optimal)^2

    Args:
        focus_position: Current focus in mm
        optimal_focus: Optimal focus position in mm
        min_fwhm: Minimum FWHM at optimal focus (pixels)
        defocus_coeff: How quickly FWHM grows with defocus

    Returns:
        Expected FWHM in pixels
    """
    defocus = focus_position - optimal_focus
    fwhm = min_fwhm + defocus_coeff * defocus**2
    return fwhm


# =============================================================================
# Mock Hardware Classes
# =============================================================================

@dataclass
class MockTelescopeStatus:
    """Mock telescope status."""
    focus_mm: float = 37.5
    ra: str = "12:34:56.7"
    dec: str = "+45:30:00.0"
    ha: str = "-01:23:45"
    airmass: float = 1.2


class MockTelescope:
    """Mock telescope for testing focus loop."""

    def __init__(self, initial_focus: float = 37.5):
        self.focus_mm = initial_focus
        self.logger = logging.getLogger(__name__)

    def set_focus(self, position: float):
        """Simulate focus move."""
        self.logger.info(f"[MockTelescope] Moving focus: {self.focus_mm:.2f} -> {position:.2f} mm")
        self.focus_mm = position

    def get_status(self) -> MockTelescopeStatus:
        """Return mock status."""
        return MockTelescopeStatus(focus_mm=self.focus_mm)


class MockCamera:
    """Mock camera for testing focus loop."""

    def __init__(self,
                 image_size: tuple = (512, 512),
                 optimal_focus: float = 37.5,
                 n_stars: int = 8):
        self.image_size = image_size
        self.optimal_focus = optimal_focus
        self.n_stars = n_stars
        self.exposure_time = 5.0
        self._telescope = None  # Link to telescope for focus position
        self.logger = logging.getLogger(__name__)

    def set_telescope(self, telescope: MockTelescope):
        """Link camera to telescope to get current focus position."""
        self._telescope = telescope

    def set_exposure(self, exposure: float):
        """Set exposure time."""
        self.exposure_time = exposure

    def capture_single(self, timeout_ms: int = 30000) -> np.ndarray:
        """Capture a simulated star field."""
        # Get current focus position
        focus = self.optimal_focus
        if self._telescope:
            focus = self._telescope.focus_mm

        # Calculate FWHM from focus position
        fwhm = focus_to_fwhm(focus, self.optimal_focus)

        self.logger.info(f"[MockCamera] Capturing at focus {focus:.2f} mm, FWHM = {fwhm:.2f} px")

        # Generate star field
        image = make_multi_star_image(
            size=self.image_size,
            n_stars=self.n_stars,
            fwhm_pixels=fwhm,
            background=200,
            noise_std=15,
            seed=int(focus * 100)  # Reproducible per focus position
        )

        return image

    def save_fits(self, frame: np.ndarray, filepath: str, header_extra: dict = None):
        """Save frame to FITS file."""
        from astropy.io import fits

        # Ensure directory exists
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        # Create FITS
        hdu = fits.PrimaryHDU(data=frame)

        # Add headers
        if header_extra:
            for key, value in header_extra.items():
                try:
                    hdu.header[key] = value
                except:
                    pass

        hdu.writeto(filepath, overwrite=True)
        self.logger.info(f"[MockCamera] Saved {filepath}")


class MockFilterWheel:
    """Mock filter wheel for testing."""

    def __init__(self, filters: List[str] = None):
        self.filters = filters or ['r', 'i', 'z']
        self.current_filter = self.filters[0]
        self.logger = logging.getLogger(__name__)

    @property
    def filter(self) -> str:
        return self.current_filter

    def goto(self, filter_name: str):
        """Move to filter."""
        if filter_name not in self.filters:
            raise ValueError(f"Unknown filter: {filter_name}")
        self.logger.info(f"[MockFilterWheel] Moving to filter: {filter_name}")
        self.current_filter = filter_name

    def wait_for_move(self, timeout: float = 30.0):
        """Wait for filter move (instant in simulation)."""
        pass


# =============================================================================
# Test Functions
# =============================================================================

def test_simulated_images():
    """Test that simulated images work with SExtractor."""
    logger.info("=" * 60)
    logger.info("Test 1: Simulated Star Images")
    logger.info("=" * 60)

    from .focus_analyzer import FocusAnalyzer

    output_dir = Path("/tmp/cerberus_focus_test")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create analyzer
    analyzer = FocusAnalyzer()

    # Test a range of FWHM values
    fwhm_values = [5.0, 8.0, 12.0, 18.0, 25.0]

    for fwhm in fwhm_values:
        # Generate image
        image = make_multi_star_image(
            size=(512, 512),
            n_stars=8,
            fwhm_pixels=fwhm,
            background=200,
            noise_std=15,
            seed=42
        )

        # Save to FITS
        from astropy.io import fits
        filepath = output_dir / f"test_fwhm_{fwhm:.0f}.fits"
        hdu = fits.PrimaryHDU(data=image)
        hdu.writeto(filepath, overwrite=True)

        # Measure FWHM
        try:
            measured_fwhm_arcsec = analyzer.measure_fwhm(str(filepath))
            measured_fwhm_px = measured_fwhm_arcsec / analyzer.plate_scale
            logger.info(f"  Input FWHM: {fwhm:.1f} px, Measured: {measured_fwhm_px:.1f} px")
        except Exception as e:
            logger.error(f"  Failed to measure FWHM={fwhm}: {e}")

    logger.info("Test 1 complete")
    return True


def test_focus_analyzer_standalone():
    """Test focus analyzer with pre-generated images."""
    logger.info("=" * 60)
    logger.info("Test 2: Focus Analyzer Standalone")
    logger.info("=" * 60)

    from .focus_analyzer import FocusAnalyzer

    output_dir = Path("/tmp/cerberus_focus_test")
    output_dir.mkdir(parents=True, exist_ok=True)

    analyzer = FocusAnalyzer()

    # Simulate focus run: 30-45mm in 2.5mm steps
    # Optimal focus at 37.5mm
    optimal_focus = 37.5
    focus_positions = np.arange(30.0, 46.0, 2.5)

    images = {}

    logger.info("Generating simulated focus sequence...")
    for focus in focus_positions:
        fwhm = focus_to_fwhm(focus, optimal_focus)

        image = make_multi_star_image(
            size=(512, 512),
            n_stars=10,
            fwhm_pixels=fwhm,
            background=200,
            noise_std=15,
            seed=int(focus * 100)
        )

        # Save
        from astropy.io import fits
        filepath = output_dir / f"focus_{focus:.1f}.fits"
        hdu = fits.PrimaryHDU(data=image)
        hdu.header['FOCUS'] = (focus, 'Focus position mm')
        hdu.writeto(filepath, overwrite=True)

        images[focus] = str(filepath)
        logger.info(f"  Focus {focus:.1f} mm: FWHM = {fwhm:.2f} px")

    # Analyze sequence
    logger.info("\nAnalyzing focus sequence...")
    result = analyzer.analyze_focus_sequence(images)

    if result.success:
        logger.info(f"\nResults:")
        logger.info(f"  Best focus: {result.best_focus:.2f} mm (expected: {optimal_focus:.1f})")
        logger.info(f"  Best FWHM: {result.best_fwhm_arcsec:.3f} arcsec")
        logger.info(f"  Fit coefficients: {result.fit_coefficients}")

        # Plot
        plot_path = output_dir / "focus_curve_test.png"
        analyzer.plot_focus_curve(result, output_path=str(plot_path))
        logger.info(f"  Plot saved to: {plot_path}")
    else:
        logger.error(f"Analysis failed: {result.error_message}")

    logger.info("Test 2 complete")
    return result.success


def test_full_focus_loop():
    """Test the full focus loop with mock hardware."""
    logger.info("=" * 60)
    logger.info("Test 3: Full Focus Loop with Mock Hardware")
    logger.info("=" * 60)

    from .focus_sequence import FocusLoop, FocusLoopConfig

    output_dir = "/tmp/cerberus_focus_loop_test"

    # Create mock hardware
    telescope = MockTelescope(initial_focus=37.5)
    camera = MockCamera(
        image_size=(512, 512),
        optimal_focus=37.5,
        n_stars=10
    )
    camera.set_telescope(telescope)

    # Configure focus loop
    config = FocusLoopConfig(
        start_position=30.0,
        end_position=45.0,
        step_size=2.5,
        exposure_time=5.0,
        output_dir=output_dir,
        settle_time=0.1,  # Fast for testing
        auto_apply_best=False  # Don't apply at end
    )

    # Create focus loop (no API - uses legacy camera capture path)
    loop = FocusLoop(
        camera=camera,
        telescope=telescope,
        config=config
    )

    # Progress callback
    def on_progress(p):
        logger.info(f"  [{p.state.value}] {p.message}")

    loop.on_progress = on_progress

    # Run
    logger.info("Running focus loop...")
    results = loop.run()

    # Check results
    result = results.get(None)  # Single filter run
    if result and result.success:
        logger.info(f"\nFocus loop results:")
        logger.info(f"  Best focus: {result.best_focus:.2f} mm (expected: 37.5)")
        logger.info(f"  Best FWHM: {result.best_fwhm_arcsec:.3f} arcsec")
        logger.info(f"  Measurements: {len(result.measurements)} positions")

        # Check plot was created
        import glob
        plots = glob.glob(f"{output_dir}/*focus_curve*.png")
        if plots:
            logger.info(f"  Plot saved: {plots[0]}")
    else:
        logger.error(f"Focus loop failed: {result.error_message if result else 'No result'}")

    logger.info("Test 3 complete")
    return result is not None and result.success


def test_focus_loop_with_filters():
    """Test focus loop with multiple filters."""
    logger.info("=" * 60)
    logger.info("Test 4: Focus Loop with Multiple Filters")
    logger.info("=" * 60)

    from .focus_sequence import FocusLoop, FocusLoopConfig

    output_dir = "/tmp/cerberus_focus_multifilter_test"

    # Create mock hardware
    telescope = MockTelescope(initial_focus=37.5)
    camera = MockCamera(
        image_size=(512, 512),
        optimal_focus=37.5,
        n_stars=10
    )
    camera.set_telescope(telescope)
    filterwheel = MockFilterWheel(filters=['r', 'i', 'z'])

    # Configure focus loop with filters
    config = FocusLoopConfig(
        start_position=32.0,
        end_position=42.0,
        step_size=2.5,
        exposure_time=5.0,
        output_dir=output_dir,
        settle_time=0.1,
        auto_apply_best=False,
        filters=['r', 'i', 'z']
    )

    # Create focus loop
    loop = FocusLoop(
        camera=camera,
        telescope=telescope,
        filterwheel=filterwheel,
        config=config
    )

    # Progress callback
    def on_progress(p):
        filter_str = f" [{p.current_filter}]" if p.current_filter else ""
        logger.info(f"  [{p.state.value}]{filter_str} {p.message}")

    loop.on_progress = on_progress

    # Run
    logger.info("Running multi-filter focus loop...")
    results = loop.run()

    # Check results for each filter
    all_success = True
    for filter_name, result in results.items():
        if result.success:
            logger.info(f"\n  Filter '{filter_name}':")
            logger.info(f"    Best focus: {result.best_focus:.2f} mm")
            logger.info(f"    Best FWHM: {result.best_fwhm_arcsec:.3f} arcsec")
        else:
            logger.error(f"  Filter '{filter_name}' failed: {result.error_message}")
            all_success = False

    logger.info("Test 4 complete")
    return all_success


def main():
    """Run all tests."""
    logger.info("=" * 60)
    logger.info("FOCUS LOOP TEST SUITE")
    logger.info("=" * 60)

    tests = [
        ("Simulated Images", test_simulated_images),
        ("Focus Analyzer Standalone", test_focus_analyzer_standalone),
        ("Full Focus Loop", test_full_focus_loop),
        ("Multi-Filter Focus Loop", test_focus_loop_with_filters),
    ]

    results = {}
    for name, test_func in tests:
        try:
            results[name] = test_func()
        except Exception as e:
            logger.exception(f"Test '{name}' raised exception: {e}")
            results[name] = False

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("TEST SUMMARY")
    logger.info("=" * 60)

    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        logger.info(f"  {name}: {status}")

    all_passed = all(results.values())
    logger.info(f"\nOverall: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
