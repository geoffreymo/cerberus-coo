"""
Telescope Hardware Interface

Wrapper around haletcs.TCSClient for P200 telescope control.
"""

from .client import TelescopeController

__all__ = ['TelescopeController']
