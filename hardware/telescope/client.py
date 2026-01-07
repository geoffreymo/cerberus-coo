# hardware/telescope/client.py
"""Telescope controller wrapper for P200 TCS."""

import logging
import threading
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


def _get_telescope_config():
    """Lazy load telescope config to avoid circular imports."""
    try:
        from ...config import get_config
        return get_config().telescope
    except Exception:
        return None


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

    # Fallback defaults if config not available
    DEFAULT_HOST = "198.202.125.194"
    DEFAULT_PORT = 49200
    DEFAULT_TIMEOUT = 30.0

    def __init__(self, host: str = None, port: int = None, timeout: float = None):
        """
        Initialize telescope controller.

        Args:
            host: TCS IP address (uses config or default)
            port: TCP port (uses config or default)
            timeout: Socket timeout in seconds (uses config or default)
        """
        if not HALETCS_AVAILABLE:
            logger.warning("haletcs package not available")

        # Load from config if available
        config = _get_telescope_config()
        if config:
            self._host = host or config.host
            self._port = port or config.port
            self._timeout = timeout or config.timeout_seconds
            self._focus_min = config.focus_min_mm
            self._focus_max = config.focus_max_mm
        else:
            self._host = host or self.DEFAULT_HOST
            self._port = port or self.DEFAULT_PORT
            self._timeout = timeout or self.DEFAULT_TIMEOUT
            self._focus_min = 1.0
            self._focus_max = 74.0

        # Two separate clients: one for commands, one for status queries
        # This prevents response mixing when polling status while sending commands
        self._cmd_client: Optional[TCSClient] = None    # For commands (set_focus, etc.)
        self._status_client: Optional[TCSClient] = None  # For queries (get_position, etc.)
        self._is_connected = False

        # Lock for command client (protects against focus thread + guiding conflicts)
        self._cmd_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        """Check if connected to TCS."""
        return self._is_connected

    def connect(self) -> bool:
        """
        Connect to the telescope control system.

        Creates two separate socket connections:
        - Command client: for focus/offset commands
        - Status client: for position/status queries

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

            # Command client
            self._cmd_client = TCSClient(
                host=self._host,
                port=self._port,
                timeout=self._timeout
            )
            self._cmd_client.connect()
            logger.info("Connected to TCS (command channel)")

            # Status client (separate socket)
            self._status_client = TCSClient(
                host=self._host,
                port=self._port,
                timeout=self._timeout
            )
            self._status_client.connect()
            logger.info("Connected to TCS (status channel)")

            self._is_connected = True
            return True

        except TCSConnectionError as e:
            logger.error(f"Failed to connect to TCS: {e}")
            self._cleanup_clients()
            return False
        except Exception as e:
            logger.error(f"Unexpected error connecting to TCS: {e}")
            self._cleanup_clients()
            return False

    def _cleanup_clients(self):
        """Clean up client connections."""
        if self._cmd_client:
            try:
                self._cmd_client.disconnect()
            except:
                pass
            self._cmd_client = None
        if self._status_client:
            try:
                self._status_client.disconnect()
            except:
                pass
            self._status_client = None
        self._is_connected = False

    def disconnect(self):
        """Disconnect from the telescope control system."""
        if not self._is_connected:
            return

        try:
            if self._cmd_client is not None:
                self._cmd_client.disconnect()
            if self._status_client is not None:
                self._status_client.disconnect()
            logger.info("Disconnected from TCS")
        except Exception as e:
            logger.error(f"Error disconnecting from TCS: {e}")
        finally:
            self._cmd_client = None
            self._status_client = None
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
        if not self._is_connected or self._cmd_client is None:
            logger.error("Not connected to TCS")
            return False

        with self._cmd_lock:
            try:
                logger.info(f"Setting focus to {position_mm:.2f} mm")
                self._cmd_client.set_focus(position_mm)
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
        if not self._is_connected or self._cmd_client is None:
            logger.error("Not connected to TCS")
            return False

        with self._cmd_lock:
            try:
                logger.info(f"Offsetting focus by {offset_mm:+.2f} mm")
                self._cmd_client.offset_focus(offset_mm)
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
        if not self._is_connected or self._status_client is None:
            return None

        try:
            status = self._status_client.get_status()
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
        if not self._is_connected or self._status_client is None:
            return None

        try:
            status = self._status_client.get_status()
            return FocusStatus(
                position_mm=status.focus_mm,
                tube_length_mm=status.tube_length_mm
            )
        except Exception as e:
            logger.error(f"Error getting focus status: {e}")
            return None

    def wait_for_focus(self, target_mm: float, tolerance_mm: float = 1.0,
                       timeout_sec: float = 60.0, poll_interval: float = 0.5) -> bool:
        """
        Wait for focus to reach target position.

        Polls the TCS until focus is within tolerance of target or timeout.

        Args:
            target_mm: Target focus position in mm
            tolerance_mm: Acceptable error in mm (default 1.0mm)
            timeout_sec: Maximum wait time in seconds (default 60s)
            poll_interval: Time between polls in seconds (default 0.5s)

        Returns:
            True if focus reached target within tolerance, False if timeout
        """
        import time

        if not self._is_connected or self._status_client is None:
            logger.error("Not connected to TCS")
            return False

        start_time = time.time()
        last_focus = None

        while (time.time() - start_time) < timeout_sec:
            try:
                current_focus = self.get_focus()
                if current_focus is None:
                    logger.warning("Failed to get focus position, retrying...")
                    time.sleep(poll_interval)
                    continue

                error = abs(current_focus - target_mm)
                if error <= tolerance_mm:
                    logger.info(f"Focus reached target: {current_focus:.2f} mm "
                               f"(target: {target_mm:.2f} mm, error: {error:.3f} mm)")
                    return True

                # Log progress if focus is moving
                if last_focus is not None and last_focus != current_focus:
                    logger.debug(f"Focus moving: {current_focus:.2f} mm -> {target_mm:.2f} mm")
                last_focus = current_focus

                time.sleep(poll_interval)

            except Exception as e:
                logger.warning(f"Error polling focus: {e}")
                time.sleep(poll_interval)

        # Timeout
        current_focus = self.get_focus()
        logger.error(f"Focus move timeout after {timeout_sec}s. "
                    f"Current: {current_focus}, Target: {target_mm:.2f} mm")
        return False

    # === Position ===

    def get_position(self) -> Optional['TelescopePosition']:
        """
        Get current telescope position.

        Returns:
            TelescopePosition with RA, Dec, etc., or None if failed
        """
        if not self._is_connected or self._status_client is None:
            return None

        try:
            return self._status_client.get_position()
        except Exception as e:
            logger.error(f"Error getting position: {e}")
            return None

    def get_status(self) -> Optional['TelescopeStatus']:
        """
        Get full telescope status.

        Returns:
            TelescopeStatus with focus, offsets, etc., or None if failed
        """
        if not self._is_connected or self._status_client is None:
            return None

        try:
            return self._status_client.get_status()
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
        if not self._is_connected or self._cmd_client is None:
            logger.error("Not connected to TCS")
            return False

        with self._cmd_lock:
            try:
                logger.info(f"Moving offset: RA {ra_arcsec:+.1f}\", Dec {dec_arcsec:+.1f}\"")
                self._cmd_client.move_offset(ra_arcsec, dec_arcsec)
                return True
            except TCSCommandError as e:
                logger.error(f"Offset move failed: {e}")
                return False
            except Exception as e:
                logger.error(f"Error moving offset: {e}")
                return False

    def move_north(self, arcsec: float) -> bool:
        """Move telescope north by given arcseconds."""
        if not self._is_connected or self._cmd_client is None:
            return False
        with self._cmd_lock:
            try:
                self._cmd_client.move_north(arcsec)
                return True
            except Exception as e:
                logger.error(f"Error moving north: {e}")
                return False

    def move_south(self, arcsec: float) -> bool:
        """Move telescope south by given arcseconds."""
        if not self._is_connected or self._cmd_client is None:
            return False
        with self._cmd_lock:
            try:
                self._cmd_client.move_south(arcsec)
                return True
            except Exception as e:
                logger.error(f"Error moving south: {e}")
                return False

    def move_east(self, arcsec: float) -> bool:
        """Move telescope east by given arcseconds."""
        if not self._is_connected or self._cmd_client is None:
            return False
        with self._cmd_lock:
            try:
                self._cmd_client.move_east(arcsec)
                return True
            except Exception as e:
                logger.error(f"Error moving east: {e}")
                return False

    def move_west(self, arcsec: float) -> bool:
        """Move telescope west by given arcseconds."""
        if not self._is_connected or self._cmd_client is None:
            return False
        with self._cmd_lock:
            try:
                self._cmd_client.move_west(arcsec)
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
