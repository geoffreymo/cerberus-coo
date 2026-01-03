"""
Cerberus Focus Loop Module

Automated focus optimization using SExtractor-based FWHM measurement.
"""

from .focus_analyzer import FocusAnalyzer, FocusResult, PLATE_SCALE
from .focus_sequence import FocusLoop, FocusLoopConfig, FocusLoopState, run_focus_loop

__all__ = [
    'FocusAnalyzer',
    'FocusResult',
    'PLATE_SCALE',
    'FocusLoop',
    'FocusLoopConfig',
    'FocusLoopState',
    'run_focus_loop',
]
