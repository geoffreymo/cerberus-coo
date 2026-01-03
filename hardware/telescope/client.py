# hardware/telescope/client.py
"""Telescope controller wrapper for P200 TCS."""

import logging
from typing import Optional
from dataclasses import dataclass

# Import from haletcs package
try:
    from haletcs import (
        TCSClient,
        TCSError,
        TCSConnectionError,
        TCSCommandError,
        TelescopePosition,
        TelescopeStatus,
    )
    HALETCS_AVAILABLE = True
except ImportError:
    HALETCS_AVAILABLE = False
    TCSClient = None
    TCSError = Exception
    TCSConnectionError = Exception
    TCSCommandError = Exception

logger = logging.getLogger(__name__)


@dataclass
class FocusStatus:
    """Current focus status."""
    position_mm: float
    tube_length_mm: float


class TelescopeController:
    """
    Wrapper for telescope control with lazy connection.

    Provides a simplified interface to the P200 TCS, with focus
    on the operations needed for high-speed imaging.

    Example usage:
        controller = TelescopeController()
        if controller.connect():
            pos = controller.get_position()
            print(f"RA: {pos.ra}, Dec: {pos.dec}")

            # Focus operations
            controller.set_focus(35.0)
            controller.offset_focus(0.5)

            controller.disconnect()
    """

    DEFAULT_HOST = "198.202.125.194"
    DEFAULT_PORT = 49200

    def __init__(self, host: str = None, port: int = None, timeout: float = 30.0):
        """
        Initialize telescope controller.

        Args:
            host: TCS IP address (default: P200 proxy)
            port: TCP port (default: 49200)
            timeout: Socket timeout in seconds
        """
        if not HALETCS_AVAILABLE:
            logger.warning("haletcs package not available")

        self._host = host or self.DEFAULT_HOST
        self._port = port or self.DEFAULT_PORT
        self._timeout = timeout
        self._client: Optional[TCSClient] = None
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        """Check if connected to TCS."""
        return self._is_connected

    def connect(self) -> bool:
        """
        Connect to the telescope control system.

        Returns:
            True if connection successful
        """
        if not HALETCS_AVAILABLE:
            logger.error("haletcs package not installed")
            return False

        if self._is_connected:
            logger.warning("Already connected to TCS")
            return True

        try:
            logger.info(f"Connecting to TCS at {self._host}:{self._port}")
            self._client = TCSClient(
                host=self._host,
                port=self._port,
                timeout=self._timeout
            )
            self._client.connect()
            self._is_connected = True
            logger.info("Connected to TCS")
            return True

        except TCSConnectionError as e:
            logger.error(f"Failed to connect to TCS: {e}")
            self._client = None
            return False
        except Exception as e:
            logger.error(f"Unexpected error connecting to TCS: {e}")
            self._client = None
            return False

    def disconnect(self):
        """Disconnect from the telescope control system."""
        if not self._is_connected:
            return

        try:
            if self._client is not None:
                self._client.disconnect()
                logger.info("Disconnected from TCS")
        except Exception as e:
            logger.error(f"Error disconnecting from TCS: {e}")
        finally:
            self._client = None
            self._is_connected = False

    # === Focus Control ===

    def set_focus(self, position_mm: float) -> bool:
        """
        Set telescope focus to absolute position.

        Args:
            position_mm: Focus position in mm (1.0-74.0)

        Returns:
            True if successful
        """
        if not self._is_connected or self._client is None:
            logger.error("Not connected to TCS")
            return False

        try:
            logger.info(f"Setting focus to {position_mm:.2f} mm")
            self._client.set_focus(position_mm)
            return True
        except TCSCommandError as e:
            logger.error(f"Focus command failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Error setting focus: {e}")
            return False

    def offset_focus(self, offset_mm: float) -> bool:
        """
        Change telescope focus by offset.

        Args:
            offset_mm: Focus offset in mm (-73.0 to 73.0)

        Returns:
            True if successful
        """
        if not self._is_connected or self._client is None:
            logger.error("Not connected to TCS")
            return False

        try:
            logger.info(f"Offsetting focus by {offset_mm:+.2f} mm")
            self._client.offset_focus(offset_mm)
            return True
        except TCSCommandError as e:
            logger.error(f"Focus offset command failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Error offsetting focus: {e}")
            return False

    def get_focus(self) -> Optional[float]:
        """
        Get current focus position.

        Returns:
            Focus position in mm, or None if failed
        """
        if not self._is_connected or self._client is None:
            return None

        try:
            status = self._client.get_status()
            return status.focus_mm
        except Exception as e:
            logger.error(f"Error getting focus: {e}")
            return None

    def get_focus_status(self) -> Optional[FocusStatus]:
        """
        Get detailed focus status.

        Returns:
            FocusStatus with position and tube length, or None if failed
        """
        if not self._is_connected or self._client is None:
            return None

        try:
            status = self._client.get_status()
            return FocusStatus(
                position_mm=status.focus_mm,
                tube_length_mm=status.tube_length_mm
            )
        except Exception as e:
            logger.error(f"Error getting focus status: {e}")
            return None

    # === Position ===

    def get_position(self) -> Optional['TelescopePosition']:
        """
        Get current telescope position.

        Returns:
            TelescopePosition with RA, Dec, etc., or None if failed
        """
        if not self._is_connected or self._client is None:
            return None

        try:
            return self._client.get_position()
        except Exception as e:
            logger.error(f"Error getting position: {e}")
            return None

    def get_status(self) -> Optional['TelescopeStatus']:
        """
        Get full telescope status.

        Returns:
            TelescopeStatus with focus, offsets, etc., or None if failed
        """
        if not self._is_connected or self._client is None:
            return None

        try:
            return self._client.get_status()
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            return None

    # === Offset Moves ===

    def move_offset(self, ra_arcsec: float, dec_arcsec: float) -> bool:
        """
        Move telescope by offset in arcseconds.

        Args:
            ra_arcsec: RA offset in arcseconds
            dec_arcsec: Dec offset in arcseconds

        Returns:
            True if successful
        """
        if not self._is_connected or self._client is None:
            logger.error("Not connected to TCS")
            return False

        try:
            logger.info(f"Moving offset: RA {ra_arcsec:+.1f}\", Dec {dec_arcsec:+.1f}\"")
            self._client.move_offset(ra_arcsec, dec_arcsec)
            return True
        except TCSCommandError as e:
            logger.error(f"Offset move failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Error moving offset: {e}")
            return False

    def move_north(self, arcsec: float) -> bool:
        """Move telescope north by given arcseconds."""
        if not self._is_connected or self._client is None:
            return False
        try:
            self._client.move_north(arcsec)
            return True
        except Exception as e:
            logger.error(f"Error moving north: {e}")
            return False

    def move_south(self, arcsec: float) -> bool:
        """Move telescope south by given arcseconds."""
        if not self._is_connected or self._client is None:
            return False
        try:
            self._client.move_south(arcsec)
            return True
        except Exception as e:
            logger.error(f"Error moving south: {e}")
            return False

    def move_east(self, arcsec: float) -> bool:
        """Move telescope east by given arcseconds."""
        if not self._is_connected or self._client is None:
            return False
        try:
            self._client.move_east(arcsec)
            return True
        except Exception as e:
            logger.error(f"Error moving east: {e}")
            return False

    def move_west(self, arcsec: float) -> bool:
        """Move telescope west by given arcseconds."""
        if not self._is_connected or self._client is None:
            return False
        try:
            self._client.move_west(arcsec)
            return True
        except Exception as e:
            logger.error(f"Error moving west: {e}")
            return False

    # === Context Manager ===

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
        return False
