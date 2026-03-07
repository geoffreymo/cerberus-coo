# api/cerberus.py
"""
Main Cerberus API for controlling the high-speed imaging system.
"""

import logging
import threading
import time
import queue
from typing import Callable, Optional, List, Dict, Any

import numpy as np

from ..hardware.camera import CameraController
from ..hardware.telescope import TelescopeController
from ..acquisition.save_thread import OptimizedSaveThread
from ..config import get_config
from .state import SystemState, CameraState

logger = logging.getLogger(__name__)

# Optional imports
try:
    from ..filterwheel.filterwheel import FilterWheel
    FILTERWHEEL_AVAILABLE = True
except ImportError:
    FILTERWHEEL_AVAILABLE = False
    FilterWheel = None

try:
    from ..focusloop import FocusLoop, FocusLoopConfig, FocusResult
    FOCUSLOOP_AVAILABLE = True
except ImportError:
    FOCUSLOOP_AVAILABLE = False
    FocusLoop = None
    FocusLoopConfig = None
    FocusResult = None

try:
    from ..hardware.gps_timing import GPSTimingDevice, GPSTimestamp
    GPS_AVAILABLE = True
except ImportError:
    GPS_AVAILABLE = False
    GPSTimingDevice = None
    GPSTimestamp = None


class CerberusAPI:
    """
    Main interface to the Cerberus high-speed imager system.

    This is the central API that scripts and GUI use to control all
    hardware components (camera, telescope, filter wheel) and manage
    data acquisition.

    Example usage:
        api = CerberusAPI()

        # Connect to hardware
        api.connect_camera()
        api.connect_telescope()

        # Configure camera
        api.set_exposure(0.1)  # 100ms

        # Start streaming and saving
        api.start_streaming()
        api.start_saving("M31", "/data/tonight")

        # Capture for 5 minutes
        time.sleep(300)

        # Stop
        api.stop_saving()
        api.stop_streaming()

        # Disconnect
        api.disconnect_camera()
        api.disconnect_telescope()
    """

    def __init__(self, cameras: List[tuple] = None):
        """
        Initialize the Cerberus API.

        Args:
            cameras: List of (camera_index, camera_id) tuples. If None, creates
                    a single camera controller for camera 0.
        """
        # Camera configuration
        if cameras is None:
            cameras = [(0, "Camera 0")]
        self._camera_list = cameras

        # Per-camera save queues and threads
        self._save_queues: Dict[int, queue.Queue] = {}
        self._save_threads: Dict[int, OptimizedSaveThread] = {}

        # Hardware controllers - one CameraController per camera
        self.cameras: Dict[int, CameraController] = {}
        for camera_index, camera_id in cameras:
            self.cameras[camera_index] = CameraController()

        # Legacy single camera reference (for backward compatibility)
        # Points to first camera
        self.camera = self.cameras[cameras[0][0]] if cameras else None

        # Shared hardware
        self.telescope = TelescopeController()
        self.filterwheel: Optional['FilterWheel'] = None
        self.gps_device: Optional['GPSTimingDevice'] = None

        # Lock for filter wheel operations (filter wheel has no internal locking)
        self._filterwheel_lock = threading.Lock()

        # System state with per-camera substates
        self._state = SystemState()
        self._state_lock = threading.Lock()

        # Initialize camera states
        for camera_index, camera_id in cameras:
            self._state.cameras[camera_index] = CameraState(
                index=camera_index,
                camera_id=camera_id
            )

        # Callbacks
        self._frame_callbacks: List[Callable] = []
        self._status_callbacks: List[Callable] = []
        self._callback_lock = threading.Lock()

        # Per-camera display frame queues (for GUI)
        self._display_queues: Dict[int, queue.Queue] = {}
        for camera_index, _ in cameras:
            self._display_queues[camera_index] = queue.Queue(maxsize=2)

        # Legacy single display queue (backward compat - points to first camera's queue)
        self._display_queue = self._display_queues[cameras[0][0]] if cameras else queue.Queue(maxsize=2)

        # Register internal frame handlers for each camera
        for camera_index, _ in cameras:
            # Create closure to capture camera_index
            def make_frame_handler(idx):
                return lambda frame, ts, fs: self._on_camera_frame(frame, ts, fs, idx)
            self.cameras[camera_index].on_frame(make_frame_handler(camera_index))

    # ==========================================================================
    # Camera Control
    # ==========================================================================

    def connect_camera(self, camera_index: int = 0) -> bool:
        """
        Connect to a camera.

        Args:
            camera_index: Camera device index

        Returns:
            True if successful
        """
        if camera_index not in self.cameras:
            logger.error(f"Camera {camera_index} not in configured cameras: {list(self.cameras.keys())}")
            return False

        logger.info(f"Connecting to camera {camera_index}...")
        controller = self.cameras[camera_index]
        success = controller.connect(camera_index)

        with self._state_lock:
            cam_state = self._state.get_camera(camera_index)
            cam_state.connected = success
            if success:
                cam_state.exposure = controller.get_exposure()

        self._notify_status_change()
        return success

    def disconnect_camera(self, camera_index: int = None):
        """
        Disconnect from a camera.

        Args:
            camera_index: Camera to disconnect. If None, disconnects all cameras.
        """
        if camera_index is None:
            # Disconnect all cameras
            for idx in list(self.cameras.keys()):
                self.disconnect_camera(idx)
            return

        if camera_index not in self.cameras:
            return

        logger.info(f"Disconnecting camera {camera_index}...")

        cam_state = self._state.get_camera(camera_index)
        if cam_state.streaming:
            self.stop_streaming(camera_index)

        self.cameras[camera_index].disconnect()

        with self._state_lock:
            cam_state.connected = False
            cam_state.streaming = False

        self._notify_status_change()

    def set_exposure(self, seconds: float, camera_index: int = 0) -> bool:
        """
        Set camera exposure time.

        Args:
            seconds: Exposure time in seconds
            camera_index: Camera to set exposure for

        Returns:
            True if successful
        """
        if camera_index not in self.cameras:
            return False

        success = self.cameras[camera_index].set_exposure(seconds)
        if success:
            with self._state_lock:
                self._state.get_camera(camera_index).exposure = seconds
            self._notify_status_change()
        return success

    def get_exposure(self, camera_index: int = 0) -> Optional[float]:
        """Get current exposure time in seconds."""
        if camera_index not in self.cameras:
            return None
        return self.cameras[camera_index].get_exposure()

    def set_binning(self, factor: int) -> bool:
        """
        Set camera binning factor.

        Args:
            factor: Binning factor (1, 2, or 4)

        Returns:
            True if successful
        """
        return self.camera.set_binning(factor)

    def set_trigger_source(self, source: str) -> bool:
        """
        Set camera trigger source.

        Args:
            source: 'internal', 'external', or 'software'

        Returns:
            True if successful
        """
        return self.camera.set_trigger_source(source)

    def set_camera_property(self, name: str, value: float, camera_index: int = 0) -> bool:
        """
        Set a camera property.

        Args:
            name: Property name
            value: Property value
            camera_index: Camera to set property on

        Returns:
            True if successful
        """
        if camera_index not in self.cameras:
            return False

        result = self.cameras[camera_index].set_property(name, value)
        if result:
            # Trigger status update so GUI reflects the change
            self.update_status()
        return result

    def get_camera_params(self, camera_index: int = 0) -> Dict[str, Any]:
        """Get all camera parameters."""
        if camera_index not in self.cameras:
            return {}
        return self.cameras[camera_index].get_all_params()

    # ==========================================================================
    # Streaming Control
    # ==========================================================================

    def start_streaming(self, camera_index: int = 0, align_to_second: bool = True) -> bool:
        """
        Start camera streaming.

        Args:
            camera_index: Camera to start streaming
            align_to_second: Align capture start to next integer second

        Returns:
            True if successful
        """
        if camera_index not in self.cameras:
            return False

        logger.info(f"Starting streaming on camera {camera_index}...")
        success = self.cameras[camera_index].start_streaming(align_to_second)

        with self._state_lock:
            cam_state = self._state.get_camera(camera_index)
            cam_state.streaming = success
            cam_state.frames_captured = 0

        self._notify_status_change()
        return success

    def stop_streaming(self, camera_index: int = 0) -> bool:
        """Stop camera streaming."""
        if camera_index not in self.cameras:
            return False

        logger.info(f"Stopping streaming on camera {camera_index}...")

        # Stop camera FIRST
        success = self.cameras[camera_index].stop_streaming()

        with self._state_lock:
            cam_state = self._state.get_camera(camera_index)
            cam_state.streaming = False

        self._notify_status_change()

        # Then stop saving (after brief pause to allow in-flight frames to be saved)
        if self._state.get_camera(camera_index).is_saving:
            import time
            time.sleep(0.2)  # 200ms like v18 GUI
            self.stop_saving(camera_index)

        return success

    def is_streaming(self, camera_index: int = 0) -> bool:
        """Check if camera is streaming."""
        if camera_index not in self.cameras:
            return False
        return self.cameras[camera_index].is_streaming()

    def capture_single(self, timeout_ms: int = 30000) -> Optional[np.ndarray]:
        """
        Capture a single frame.

        Args:
            timeout_ms: Timeout in milliseconds

        Returns:
            Frame data as numpy array, or None if failed
        """
        return self.camera.capture_single(timeout_ms)

    def capture_single_to_fits(
        self,
        filepath: str,
        object_name: str = "single",
        comment: str = "",
        extra_headers: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000
    ) -> Optional[str]:
        """
        Capture a single frame and save to FITS file.

        Uses the same header structure as regular streaming captures.

        Args:
            filepath: Output FITS file path
            object_name: Object name for FITS header
            comment: Optional comment for FITS header
            extra_headers: Optional dict of additional header items
                           e.g. {'FOCUS': (35.0, 'Focus position mm')}
            timeout_ms: Capture timeout in milliseconds

        Returns:
            Filepath if successful, None if failed
        """
        import os
        import time
        import warnings
        from datetime import datetime, timezone
        from astropy.io import fits
        from astropy.io.fits.verify import VerifyWarning

        # Suppress HIERARCH keyword warnings
        warnings.filterwarnings('ignore', category=VerifyWarning, message='.*HIERARCH.*')
        warnings.filterwarnings('ignore', category=VerifyWarning, message='.*Card is too long.*')

        if not self._state.camera_connected:
            logger.error("Cannot capture: camera not connected")
            return None

        if self._state.camera_streaming:
            logger.error("Cannot capture single while streaming")
            return None

        # Capture frame
        logger.info(f"Capturing single frame to {filepath}")
        frame = self.camera.capture_single(timeout_ms)
        if frame is None:
            logger.error("Failed to capture frame")
            return None

        capture_time = time.time()

        try:
            # Ensure output directory exists
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            # Build FITS header (same structure as save_thread)
            primary_hdr = fits.Header()
            primary_hdr['OBJECT'] = (object_name, 'Object name')
            primary_hdr['NFRAMES'] = (1, 'Number of frames')

            # UTC timestamp
            utc_now = datetime.now(timezone.utc)
            primary_hdr['DATE-OBS'] = (utc_now.isoformat(), 'UTC observation time')

            # Comment if provided
            if comment:
                primary_hdr['COMMENT'] = comment

            # Filter name
            if self.filterwheel and self._state.filterwheel_connected:
                try:
                    filter_name = self.filterwheel.filter
                    if filter_name:
                        primary_hdr['FILTER'] = (filter_name, 'Filter name')
                except:
                    pass

            # Exposure time
            if self._state.camera_exposure:
                primary_hdr['EXPTIME'] = (self._state.camera_exposure, 'Exposure time (s)')

            # Telescope info if connected - query fresh data
            if self._state.telescope_connected and self.telescope:
                try:
                    position = self.telescope.get_position()
                    status = self.telescope.get_status()

                    if position:
                        primary_hdr['TELRA'] = (position.ra, 'Right Ascension')
                        primary_hdr['TELDEC'] = (position.dec, 'Declination')
                        primary_hdr['TELHA'] = (position.ha, 'Hour Angle')
                        primary_hdr['TELLST'] = (position.lst, 'Local Sidereal Time')
                        primary_hdr['AIRMASS'] = (position.airmass, 'Airmass')
                        primary_hdr['TELUTC'] = (position.utc_time, 'UTC time from TCS')
                        primary_hdr['TELDAY'] = (position.utc_day, 'UTC day number')

                    if status:
                        primary_hdr['TELFOCUS'] = (status.focus_mm, 'Focus position (mm)')
                        primary_hdr['TUBELEN'] = (status.tube_length_mm, 'Tube length (mm)')
                        primary_hdr['HIERARCH TEL OFFSET_RA'] = (status.offset_ra_arcsec, 'RA offset (arcsec)')
                        primary_hdr['HIERARCH TEL OFFSET_DEC'] = (status.offset_dec_arcsec, 'Dec offset (arcsec)')
                        primary_hdr['HIERARCH TEL RATE_RA'] = (status.rate_ra_arcsec_hr, 'RA rate (arcsec/hr)')
                        primary_hdr['HIERARCH TEL RATE_DEC'] = (status.rate_dec_arcsec_hr, 'Dec rate (arcsec/hr)')
                        primary_hdr['CASSRING'] = (status.cass_ring_angle, 'Cass ring angle (deg)')
                        primary_hdr['TELID'] = (status.telescope_id, 'Telescope ID')
                except Exception as e:
                    logger.warning(f"Could not get telescope data for FITS header: {e}")

            # Extra headers (e.g. focus position for focus loop)
            if extra_headers:
                for key, value in extra_headers.items():
                    try:
                        primary_hdr[key] = value
                    except:
                        pass

            # Primary HDU (header only)
            primary_hdu = fits.PrimaryHDU(header=primary_hdr)

            # ImageHDU for data
            image_hdu = fits.ImageHDU(data=frame)
            image_hdu.header['EXTNAME'] = 'DATA'

            # Add camera parameters using HIERARCH for long keys
            camera_params = self.camera.get_all_params()
            for key, value in camera_params.items():
                try:
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore")
                        if len(key) > 8:
                            image_hdu.header[f'HIERARCH CAM {key}'] = value
                        else:
                            image_hdu.header[key] = value
                except:
                    pass

            # BinTableHDU for timestamp (single entry)
            col1 = fits.Column(name='TIMESTAMP', format='D', array=[capture_time])
            col2 = fits.Column(name='FRAMESTAMP', format='K', array=[0])
            timestamp_hdu = fits.BinTableHDU.from_columns([col1, col2])
            timestamp_hdu.header['EXTNAME'] = 'TIMESTAMPS'

            # Write FITS file
            hdul = fits.HDUList([primary_hdu, image_hdu, timestamp_hdu])
            hdul.writeto(filepath, overwrite=True)

            file_size_mb = os.path.getsize(filepath) / 1e6
            logger.info(f"Saved {filepath} ({file_size_mb:.1f} MB)")

            return filepath

        except Exception as e:
            logger.error(f"Failed to save FITS: {e}")
            return None

    # ==========================================================================
    # Acquisition Control
    # ==========================================================================

    def start_saving(
        self,
        object_name: str,
        output_dir: str,
        frames_per_cube: int = 100,
        comment: str = "",
        camera_index: int = 0
    ) -> bool:
        """
        Start saving frames to FITS cubes.

        Args:
            object_name: Object name for filenames and headers
            output_dir: Output directory
            frames_per_cube: Number of frames per FITS cube
            comment: Optional comment for FITS headers
            camera_index: Camera to save frames from

        Returns:
            True if successful
        """
        if camera_index not in self.cameras:
            logger.error(f"Cannot save: camera {camera_index} not found")
            return False

        cam_state = self._state.get_camera(camera_index)
        if not cam_state.streaming:
            logger.error(f"Cannot save: camera {camera_index} not streaming")
            return False

        # Get camera ID for subdirectory
        camera_id = cam_state.camera_id or f"cam{camera_index}"
        logger.info(f"Starting save on camera {camera_index} ({camera_id}): {object_name} to {output_dir}")

        # Create save queue (large capacity for high-speed capture)
        save_queue = queue.Queue(maxsize=50000)
        self._save_queues[camera_index] = save_queue

        # Give camera access to the save queue
        self.cameras[camera_index].save_queue = save_queue

        # Build header dict with timing and telescope info
        header_dict = {}
        if comment:
            header_dict['COMMENT'] = (comment, 'User comment')
        # Add camera ID to header
        header_dict['CAMERAID'] = (camera_id, 'Camera identifier')

        # Create date-based subdirectory with camera ID inside
        # Structure: output_dir/captures_YYYY_MM_DD/PHX2/
        from datetime import datetime
        import os
        date_str = datetime.now().strftime('%Y_%m_%d')
        save_folder = os.path.join(output_dir, f"captures_{date_str}", camera_id)

        # Create filter callback
        def get_current_filter():
            """Get current filter name for FITS header"""
            if self.filterwheel and self._state.filterwheel_connected:
                try:
                    return self.filterwheel.filter
                except:
                    return None
            return None

        # Create telescope callback - reads from cached state (updated by polling)
        def get_telescope_data():
            """Get current telescope position and status from cached state for FITS header"""
            if not self._state.telescope_connected:
                return None, None

            # Read from cached state - no TCS query needed
            # State is updated every 500ms by update_status()
            state = self._state

            # Build position dict from cached state
            if state.telescope_ra is not None:
                pos_dict = {
                    'ra': state.telescope_ra,
                    'dec': state.telescope_dec,
                    'ha': state.telescope_ha,
                    'lst': state.telescope_lst,
                    'airmass': state.telescope_airmass,
                    'utc_time': state.telescope_utc_time,
                    'utc_day': state.telescope_utc_day,
                }
            else:
                pos_dict = None

            # Build status dict from cached state
            if state.telescope_focus is not None:
                status_dict = {
                    'focus_mm': state.telescope_focus,
                    'tube_length_mm': state.telescope_tube_length_mm,
                    'offset_ra_arcsec': state.telescope_offset_ra_arcsec,
                    'offset_dec_arcsec': state.telescope_offset_dec_arcsec,
                    'rate_ra_arcsec_hr': state.telescope_rate_ra_arcsec_hr,
                    'rate_dec_arcsec_hr': state.telescope_rate_dec_arcsec_hr,
                    'cass_ring_angle': state.telescope_cass_ring_angle,
                    'telescope_id': state.telescope_id,
                }
            else:
                status_dict = None

            return pos_dict, status_dict

        # Create GPS start callback - returns GPS timestamp of first frame
        def get_gps_start():
            """Get GPS timestamp of first frame for FITS header"""
            return self.cameras[camera_index].get_gps_start_timestamp()

        # Create and start save thread with ProcessPoolExecutor
        save_thread = OptimizedSaveThread(
            save_queue=save_queue,
            output_dir=save_folder,
            object_name=object_name,
            header_dict=header_dict,
            frames_per_cube=frames_per_cube,
            camera_params=self.cameras[camera_index].get_all_params(),
            filter_callback=get_current_filter,
            telescope_callback=get_telescope_data,
            camera_id=camera_id,
            gps_start_callback=get_gps_start
        )
        self._save_threads[camera_index] = save_thread
        save_thread.start()

        # Legacy reference for backward compat (points to first camera's save thread)
        if camera_index == self._camera_list[0][0]:
            self.save_thread = save_thread
            self.save_queue = save_queue

        with self._state_lock:
            cam_state = self._state.get_camera(camera_index)
            cam_state.is_saving = True
            cam_state.save_object_name = object_name
            cam_state.save_output_dir = save_folder
            cam_state.frames_saved = 0
            cam_state.frames_dropped = 0
            cam_state.cubes_saved = 0

        self._notify_status_change()
        return True

    def stop_saving(self, camera_index: int = None):
        """Stop saving frames.

        Args:
            camera_index: Camera to stop saving. If None, stops all cameras.
        """
        if camera_index is None:
            # Stop all cameras
            for idx in list(self._save_threads.keys()):
                self.stop_saving(idx)
            return

        logger.info(f"Stopping save on camera {camera_index}...")

        # Set is_saving=False FIRST so camera stops putting frames in queue
        with self._state_lock:
            cam_state = self._state.get_camera(camera_index)
            cam_state.is_saving = False

        # Remove save queue from camera
        if camera_index in self.cameras:
            self.cameras[camera_index].save_queue = None

        # Stop and join save thread
        save_thread = self._save_threads.get(camera_index)
        if save_thread and save_thread.is_alive():
            save_thread.stop()
            save_thread.join(timeout=60)  # Wait up to 60s for writes to complete

            # Update stats from save thread
            with self._state_lock:
                cam_state = self._state.get_camera(camera_index)
                cam_state.frames_saved = save_thread.total_frames_saved
                cam_state.frames_dropped = save_thread.total_frames_dropped
                cam_state.cubes_saved = save_thread.cubes_written

        # Clean up
        if camera_index in self._save_threads:
            del self._save_threads[camera_index]
        if camera_index in self._save_queues:
            del self._save_queues[camera_index]

        # Update legacy references
        if camera_index == self._camera_list[0][0]:
            self.save_thread = None
            self.save_queue = None

        self._notify_status_change()

    def is_saving(self) -> bool:
        """Check if currently saving."""
        return self.save_thread is not None and self.save_thread.is_alive()

    # ==========================================================================
    # Telescope Control
    # ==========================================================================

    def connect_telescope(self, host: str = None, port: int = None) -> bool:
        """
        Connect to the telescope.

        Args:
            host: TCS host address
            port: TCS port

        Returns:
            True if successful
        """
        logger.info("Connecting to telescope...")

        if host or port:
            self.telescope = TelescopeController(host=host, port=port)

        success = self.telescope.connect()

        with self._state_lock:
            self._state.telescope_connected = success
            if success:
                self._state.telescope_focus = self.telescope.get_focus()
                pos = self.telescope.get_position()
                if pos:
                    self._state.telescope_ra = pos.ra
                    self._state.telescope_dec = pos.dec
                    self._state.telescope_ha = pos.ha
                    self._state.telescope_lst = pos.lst
                    self._state.telescope_airmass = pos.airmass
                    self._state.telescope_utc = f"{pos.utc_day} {pos.utc_time}"

        self._notify_status_change()
        return success

    def disconnect_telescope(self):
        """Disconnect from the telescope."""
        logger.info("Disconnecting telescope...")
        self.telescope.disconnect()

        with self._state_lock:
            self._state.telescope_connected = False
            self._state.telescope_focus = None
            self._state.telescope_ra = None
            self._state.telescope_dec = None
            self._state.telescope_ha = None
            self._state.telescope_lst = None
            self._state.telescope_airmass = None
            self._state.telescope_utc = None

        self._notify_status_change()

    def set_focus(self, position_mm: float) -> bool:
        """
        Set telescope focus to absolute position.

        Args:
            position_mm: Focus position in mm (1.0-74.0)

        Returns:
            True if successful
        """
        previous_focus = self._state.telescope_focus
        success = self.telescope.set_focus(position_mm)
        if success:
            with self._state_lock:
                self._state.telescope_focus = position_mm
            self._notify_status_change()
            # Log focus change for operational record
            logger.info(f"FOCUS CHANGE: {previous_focus:.2f} -> {position_mm:.2f} mm")
        else:
            logger.warning(f"FOCUS CHANGE FAILED: {previous_focus} -> {position_mm:.2f} mm")
        return success

    def offset_focus(self, offset_mm: float) -> bool:
        """
        Offset telescope focus.

        Args:
            offset_mm: Focus offset in mm

        Returns:
            True if successful
        """
        previous_focus = self._state.telescope_focus
        success = self.telescope.offset_focus(offset_mm)
        if success:
            focus = self.telescope.get_focus()
            with self._state_lock:
                self._state.telescope_focus = focus
            self._notify_status_change()
            # Log focus change for operational record
            logger.info(f"FOCUS OFFSET: {previous_focus:.2f} + ({offset_mm:+.2f}) = {focus:.2f} mm")
        else:
            logger.warning(f"FOCUS OFFSET FAILED: {previous_focus} + ({offset_mm:+.2f}) mm")
        return success

    def get_focus(self) -> Optional[float]:
        """Get current focus position in mm."""
        return self.telescope.get_focus()

    def move_offset(self, ra_arcsec: float, dec_arcsec: float) -> bool:
        """
        Move telescope by offset.

        Args:
            ra_arcsec: RA offset in arcseconds
            dec_arcsec: Dec offset in arcseconds

        Returns:
            True if successful
        """
        return self.telescope.move_offset(ra_arcsec, dec_arcsec)

    # ==========================================================================
    # Filter Wheel Control
    # ==========================================================================

    def connect_filterwheel(self, config_path: str = None) -> bool:
        """
        Connect to the filter wheel.

        Uses filter configuration from main config.json by default.
        A separate config_path can be provided for legacy support.

        Args:
            config_path: Path to filter configuration JSON (optional, uses main config if None)

        Returns:
            True if successful
        """
        if not FILTERWHEEL_AVAILABLE:
            logger.error("FilterWheel module not available")
            return False

        try:
            logger.info("Connecting to filter wheel...")
            config = get_config()

            if config_path:
                # Legacy: use separate config file
                self.filterwheel = FilterWheel(
                    library_path=config.filterwheel.library_path,
                    config_path=config_path
                )
            else:
                # Use main config
                self.filterwheel = FilterWheel(
                    library_path=config.filterwheel.library_path,
                    filters=config.filterwheel.filters
                )

            with self._state_lock:
                self._state.filterwheel_connected = True
                self._state.current_filter = self.filterwheel.filter
                self._state.available_filters = list(self.filterwheel.filters.values())

            self._notify_status_change()
            return True

        except Exception as e:
            logger.error(f"Failed to connect filter wheel: {e}")
            self.filterwheel = None
            return False

    def disconnect_filterwheel(self):
        """Disconnect from the filter wheel."""
        if self.filterwheel is not None:
            try:
                self.filterwheel.close()
            except:
                pass
            self.filterwheel = None

        with self._state_lock:
            self._state.filterwheel_connected = False
            self._state.current_filter = None

        self._notify_status_change()

    # ==========================================================================
    # GPS Timing
    # ==========================================================================

    def connect_gps(self) -> bool:
        """
        Connect to the GPS timing device.

        The GPS device provides precision timestamps from the Meinberg UCAP buffer.
        It is shared across all cameras.

        Returns:
            True if successful
        """
        if not GPS_AVAILABLE:
            logger.warning("GPS timing module not available")
            return False

        if self.gps_device is not None:
            logger.info("GPS device already connected")
            return True

        try:
            self.gps_device = GPSTimingDevice()
            if self.gps_device.connect():
                logger.info("GPS timing device connected")

                # Share with all cameras
                for cam in self.cameras.values():
                    cam.set_gps_device(self.gps_device)

                with self._state_lock:
                    self._state.gps_connected = True

                self._notify_status_change()
                return True
            else:
                logger.warning("Failed to connect GPS device")
                self.gps_device = None
                return False

        except Exception as e:
            logger.error(f"GPS connection error: {e}")
            self.gps_device = None
            return False

    def disconnect_gps(self):
        """Disconnect from the GPS timing device."""
        if self.gps_device is not None:
            # Remove from all cameras
            for cam in self.cameras.values():
                cam.set_gps_device(None)

            try:
                self.gps_device.disconnect()
            except:
                pass
            self.gps_device = None

        with self._state_lock:
            self._state.gps_connected = False

        self._notify_status_change()

    def is_gps_connected(self) -> bool:
        """Check if GPS device is connected."""
        return self.gps_device is not None and self.gps_device.is_connected

    def set_filter(self, name: str, apply_focus: bool = True) -> bool:
        """
        Set filter by name, optionally moving to its calibrated focus position.

        When apply_focus is True and the telescope is connected,
        the focus will be set to the calibrated absolute position for this filter.

        Args:
            name: Filter name
            apply_focus: Move to calibrated focus position (default True)

        Returns:
            True if successful
        """
        if self.filterwheel is None:
            logger.error("Filter wheel not connected")
            return False

        # Serialize filter wheel access (hardware has no internal locking)
        with self._filterwheel_lock:
            try:
                # Get previous filter for logging
                previous_filter = self._state.current_filter

                # Change filter
                self.filterwheel.filter = name
                self.filterwheel.wait_for_move()

                with self._state_lock:
                    self._state.current_filter = name

                # Log filter change for operational record
                logger.info(f"FILTER CHANGE: {previous_filter} -> {name}")

                # Apply focus position if requested and telescope is connected
                if apply_focus and self._state.telescope_connected:
                    self._apply_filter_focus_position(name)

                self._notify_status_change()
                return True

            except Exception as e:
                logger.error(f"FILTER CHANGE FAILED: {self._state.current_filter} -> {name}: {e}")
                return False

    def _apply_filter_focus_position(self, filter_name: str):
        """
        Move to calibrated focus position for a filter.

        Args:
            filter_name: Filter name
        """
        try:
            config = get_config()
            focus_position = config.get_filter_focus_position(filter_name)

            if focus_position is not None:
                logger.info(f"Moving to calibrated focus for {filter_name}: {focus_position:.2f} mm")
                self.set_focus(focus_position)
            else:
                logger.debug(f"No calibrated focus position for {filter_name}")

        except Exception as e:
            logger.warning(f"Could not apply focus position: {e}")

    def get_filter(self) -> Optional[str]:
        """Get current filter name."""
        if self.filterwheel is None:
            return None
        return self.filterwheel.filter

    def get_available_filters(self) -> List[str]:
        """Get list of available filter names."""
        if self.filterwheel is None:
            return []
        return list(self.filterwheel.filters.values())

    def get_filter_focus_position(self, filter_name: str = None) -> Optional[float]:
        """
        Get calibrated focus position for a filter.

        Args:
            filter_name: Filter name (uses current filter if None)

        Returns:
            Focus position in mm, or None if not calibrated
        """
        if filter_name is None:
            filter_name = self._state.current_filter
        if filter_name is None:
            return None

        config = get_config()
        return config.get_filter_focus_position(filter_name)

    def set_filter_focus_position(self, filter_name: str, position_mm: float, save: bool = True) -> bool:
        """
        Set calibrated focus position for a filter.

        Args:
            filter_name: Filter name
            position_mm: Absolute focus position in mm
            save: Save to config file (default True)

        Returns:
            True if successful
        """
        try:
            config = get_config()
            config.set_filter_focus_position(filter_name, position_mm)
            logger.info(f"Set focus position for {filter_name}: {position_mm:.2f} mm")

            if save:
                self._save_config()

            return True
        except Exception as e:
            logger.error(f"Failed to set focus position: {e}")
            return False

    def calibrate_filter_focus(self) -> bool:
        """
        Calibrate focus positions for all filters.

        Runs a focus loop for each filter and saves the absolute best focus position.

        This is a long-running operation - run focus loop for each filter.

        Returns:
            True if successful
        """
        if not FOCUSLOOP_AVAILABLE:
            logger.error("FocusLoop module not available")
            return False

        filters = self.get_available_filters()
        if not filters:
            logger.error("No filters available")
            return False

        starting_filter = self._state.current_filter or filters[0]
        logger.info(f"Calibrating focus positions for all filters")

        # Run focus loop for each filter
        success_count = 0

        for filter_name in filters:
            logger.info(f"Running focus loop for filter: {filter_name}")
            self.set_filter(filter_name, apply_focus=False)

            result = self.run_focus_loop()
            if result and result.success:
                self.set_filter_focus_position(filter_name, result.best_focus, save=False)
                logger.info(f"  Calibrated {filter_name}: {result.best_focus:.2f} mm")
                success_count += 1
            else:
                logger.warning(f"  Focus loop failed for {filter_name}")

        # Save all positions
        self._save_config()

        # Return to starting filter
        self.set_filter(starting_filter, apply_focus=True)

        logger.info(f"Calibration complete: {success_count}/{len(filters)} filters calibrated")
        return success_count > 0

    def _save_config(self):
        """Save current config to file."""
        try:
            import json
            from ..config import DEFAULT_CONFIG_PATH

            config = get_config()

            # Build config dict
            config_dict = {
                "telescope": {
                    "host": config.telescope.host,
                    "port": config.telescope.port,
                    "timeout_seconds": config.telescope.timeout_seconds,
                    "focus_min_mm": config.telescope.focus_min_mm,
                    "focus_max_mm": config.telescope.focus_max_mm,
                    "auto_connect": config.telescope.auto_connect
                },
                "camera": {
                    "buffer_size": config.camera.buffer_size,
                    "defaults": config.camera.defaults,
                    "capture_timeout_ms": config.camera.capture_timeout_ms,
                    "align_to_second_offset": config.camera.align_to_second_offset
                },
                "filterwheel": {
                    "library_path": config.filterwheel.library_path,
                    "filters": config.filterwheel.filters,
                    "focus_positions_mm": config.filterwheel.focus_positions_mm
                },
                "focusloop": {
                    "start_position_mm": config.focusloop.start_position_mm,
                    "end_position_mm": config.focusloop.end_position_mm,
                    "step_size_mm": config.focusloop.step_size_mm,
                    "exposure_time_seconds": config.focusloop.exposure_time_seconds,
                    "settle_time_seconds": config.focusloop.settle_time_seconds,
                    "max_fwhm_arcsec": config.focusloop.max_fwhm_arcsec,
                    "auto_apply_best": config.focusloop.auto_apply_best
                },
                "instrument": {
                    "plate_scale_arcsec_per_pixel": config.instrument.plate_scale_arcsec_per_pixel,
                    "saturation_level_adu": config.instrument.saturation_level_adu,
                    "min_fwhm_pixels": config.instrument.min_fwhm_pixels,
                    "fwhm_box_size_pixels": config.instrument.fwhm_box_size_pixels,
                    "timestamp_rollover_threshold": config.instrument.timestamp_rollover_threshold,
                    "framestamp_rollover_threshold": config.instrument.framestamp_rollover_threshold
                },
                "acquisition": {
                    "max_queue_size": config.acquisition.max_queue_size,
                    "max_pending_writes": config.acquisition.max_pending_writes,
                    "frames_per_cube": config.acquisition.frames_per_cube,
                    "thread_pool_workers": config.acquisition.thread_pool_workers,
                    "backpressure_threshold": config.acquisition.backpressure_threshold
                },
                "paths": {
                    "default_output_dir": config.paths.default_output_dir,
                    "focus_output_dir": config.paths.focus_output_dir
                },
                "gui": {
                    "status_update_interval_ms": config.gui.status_update_interval_ms,
                    "default_object_name": config.gui.default_object_name,
                    "default_focus_display_mm": config.gui.default_focus_display_mm
                },
                "guiding": {
                    "averaging_window_seconds": config.guiding.averaging_window_seconds,
                    "correction_threshold_arcsec": config.guiding.correction_threshold_arcsec,
                    "max_correction_arcsec": config.guiding.max_correction_arcsec,
                    "correction_interval_seconds": config.guiding.correction_interval_seconds,
                    "guide_gain": config.guiding.guide_gain,
                    "x_to_ra_sign": config.guiding.x_to_ra_sign,
                    "y_to_dec_sign": config.guiding.y_to_dec_sign
                }
            }

            with open(DEFAULT_CONFIG_PATH, 'w') as f:
                json.dump(config_dict, f, indent=4)

            logger.info(f"Saved config to: {DEFAULT_CONFIG_PATH}")

        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    # ==========================================================================
    # Focus Loop
    # ==========================================================================

    def run_focus_loop(self, config: 'FocusLoopConfig' = None,
                        on_progress: Callable = None) -> Optional[Dict]:
        """
        Run automated focus loop.

        Supports multi-filter focus runs when filterwheel is connected
        and config.filters is specified.

        Args:
            config: Focus loop configuration
            on_progress: Optional callback for progress updates

        Returns:
            Dict mapping filter_name (or None) -> FocusResult, or None if failed
        """
        if not FOCUSLOOP_AVAILABLE:
            logger.error("FocusLoop module not available")
            return None

        if not self._state.telescope_connected:
            logger.error("Cannot run focus loop: telescope not connected")
            return None

        if not self._state.camera_connected:
            logger.error("Cannot run focus loop: camera not connected")
            return None

        if self._state.camera_streaming:
            logger.error("Cannot run focus loop while streaming")
            return None

        logger.info("Starting focus loop...")

        with self._state_lock:
            self._state.focus_loop_running = True
            self._state.focus_loop_progress = 0

        self._notify_status_change()

        try:
            # Create focus loop with our hardware and API for unified imaging
            focus_loop = FocusLoop(
                camera=self.camera,
                telescope=self.telescope,
                filterwheel=self.filterwheel,
                config=config,
                api=self
            )

            # Store reference for abort
            self._focus_loop = focus_loop

            # Set progress callback
            if on_progress:
                focus_loop.on_progress = on_progress

            results = focus_loop.run()

            # Log results with old vs new comparison and save to config
            config = get_config()
            logger.info("=" * 50)
            logger.info("FOCUS LOOP RESULTS")
            logger.info("-" * 50)
            for filter_name, result in results.items():
                if result.success:
                    fname = filter_name or self._state.current_filter or "unknown"
                    old_pos = config.get_filter_focus_position(fname)
                    old_str = f"{old_pos:.2f}" if old_pos is not None else "None"
                    logger.info(f"  {fname:8s}: old={old_str:>6s} mm -> new={result.best_focus:.2f} mm  "
                               f"(FWHM: {result.best_fwhm_arcsec:.2f}\")")
                    # Save the calibrated focus position
                    self.set_filter_focus_position(fname, result.best_focus, save=False)
                else:
                    fname = filter_name or self._state.current_filter or "unknown"
                    logger.info(f"  {fname:8s}: FAILED")
            logger.info("=" * 50)

            # Save all calibrated positions to config file
            self._save_config()
            logger.info("Saved calibrated focus positions to config")

            return results

        except Exception as e:
            logger.error(f"Focus loop error: {e}")
            with self._state_lock:
                self._state.add_error(f"Focus loop error: {e}")
            return None

        finally:
            self._focus_loop = None
            with self._state_lock:
                self._state.focus_loop_running = False
            self._notify_status_change()

    def abort_focus_loop(self):
        """Abort a running focus loop."""
        if hasattr(self, '_focus_loop') and self._focus_loop:
            self._focus_loop.abort()
            logger.info("Focus loop abort requested")

    def save_current_filter_focus(self) -> bool:
        """
        Save the current telescope focus position for the current filter.

        Use this after running a focus loop to save the calibrated position.

        Returns:
            True if successful
        """
        if not self._state.telescope_connected:
            logger.error("Cannot save filter focus: telescope not connected")
            return False

        current_filter = self._state.current_filter
        if not current_filter:
            logger.error("Cannot save filter focus: no filter selected")
            return False

        current_focus = self.get_focus()
        if current_focus is None:
            logger.error("Cannot save filter focus: failed to get current focus")
            return False

        # Log old vs new
        old_pos = self.get_filter_focus_position(current_filter)
        old_str = f"{old_pos:.2f}" if old_pos is not None else "None"
        logger.info(f"SAVING FILTER FOCUS: {current_filter}: {old_str} mm -> {current_focus:.2f} mm")

        return self.set_filter_focus_position(current_filter, current_focus, save=True)

    # ==========================================================================
    # Callbacks
    # ==========================================================================

    def on_frame(self, callback: Callable[[np.ndarray, float, int], None]):
        """
        Register a callback for frame delivery.

        Callback signature: callback(frame, timestamp, framestamp)
        """
        with self._callback_lock:
            if callback not in self._frame_callbacks:
                self._frame_callbacks.append(callback)

    def remove_frame_callback(self, callback: Callable):
        """Remove a previously registered frame callback."""
        with self._callback_lock:
            if callback in self._frame_callbacks:
                self._frame_callbacks.remove(callback)

    def on_status_change(self, callback: Callable[['SystemState'], None]):
        """
        Register a callback for status changes.

        Callback signature: callback(state)
        """
        with self._callback_lock:
            if callback not in self._status_callbacks:
                self._status_callbacks.append(callback)

    def remove_status_callback(self, callback: Callable):
        """Remove a previously registered status callback."""
        with self._callback_lock:
            if callback in self._status_callbacks:
                self._status_callbacks.remove(callback)

    def get_display_frame(self, camera_index: int = 0, timeout: float = 0.1) -> Optional[np.ndarray]:
        """
        Get latest frame for display.

        This is designed for GUI use - returns the most recent frame
        from a small queue.

        Args:
            camera_index: Camera to get frame from
            timeout: Timeout in seconds

        Returns:
            Frame data or None if no frame available
        """
        display_queue = self._display_queues.get(camera_index, self._display_queue)
        try:
            return display_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ==========================================================================
    # State
    # ==========================================================================

    def get_cameras(self) -> List[tuple]:
        """Get list of (camera_index, camera_id) tuples."""
        return self._camera_list.copy()

    @property
    def state(self) -> SystemState:
        """Get current system state (copy)."""
        with self._state_lock:
            # Create a deep copy of state including camera states
            state_copy = SystemState()
            # Copy camera states
            for idx, cam in self._state.cameras.items():
                state_copy.cameras[idx] = CameraState(
                    index=cam.index,
                    camera_id=cam.camera_id,
                    connected=cam.connected,
                    streaming=cam.streaming,
                    exposure=cam.exposure,
                    temperature=cam.temperature,
                    frame_rate=cam.frame_rate,
                    params=cam.params.copy(),
                    frames_captured=cam.frames_captured,
                    frames_saved=cam.frames_saved,
                    frames_dropped=cam.frames_dropped,
                    cubes_saved=cam.cubes_saved,
                    is_saving=cam.is_saving,
                    save_object_name=cam.save_object_name,
                    save_output_dir=cam.save_output_dir,
                )
            # Copy shared state
            state_copy.telescope_connected = self._state.telescope_connected
            state_copy.telescope_focus = self._state.telescope_focus
            state_copy.telescope_ra = self._state.telescope_ra
            state_copy.telescope_dec = self._state.telescope_dec
            state_copy.telescope_ha = self._state.telescope_ha
            state_copy.telescope_lst = self._state.telescope_lst
            state_copy.telescope_airmass = self._state.telescope_airmass
            state_copy.telescope_utc = self._state.telescope_utc
            state_copy.telescope_utc_day = self._state.telescope_utc_day
            state_copy.telescope_utc_time = self._state.telescope_utc_time
            state_copy.telescope_tube_length_mm = self._state.telescope_tube_length_mm
            state_copy.telescope_offset_ra_arcsec = self._state.telescope_offset_ra_arcsec
            state_copy.telescope_offset_dec_arcsec = self._state.telescope_offset_dec_arcsec
            state_copy.telescope_rate_ra_arcsec_hr = self._state.telescope_rate_ra_arcsec_hr
            state_copy.telescope_rate_dec_arcsec_hr = self._state.telescope_rate_dec_arcsec_hr
            state_copy.telescope_cass_ring_angle = self._state.telescope_cass_ring_angle
            state_copy.telescope_id = self._state.telescope_id
            state_copy.filterwheel_connected = self._state.filterwheel_connected
            state_copy.current_filter = self._state.current_filter
            state_copy.available_filters = self._state.available_filters.copy()
            state_copy.focus_loop_running = self._state.focus_loop_running
            state_copy.focus_loop_progress = self._state.focus_loop_progress
            state_copy.focus_loop_total_steps = self._state.focus_loop_total_steps
            state_copy.last_error = self._state.last_error
            state_copy.errors = self._state.errors.copy()
            return state_copy

    def update_status(self):
        """Update system status from hardware."""
        # Read shared hardware values outside lock
        telescope_focus = None
        telescope_pos = None
        telescope_status = None
        current_filter = None

        # Check shared connection status with minimal lock
        with self._state_lock:
            telescope_connected = self._state.telescope_connected
            filterwheel_connected = self._state.filterwheel_connected

        # Read shared hardware (no lock held)
        try:
            if telescope_connected:
                telescope_focus = self.telescope.get_focus()
                telescope_pos = self.telescope.get_position()
                telescope_status = self.telescope.get_status()

            if filterwheel_connected and self.filterwheel:
                current_filter = self.filterwheel.filter

        except Exception as e:
            logger.error(f"Error reading shared hardware status: {e}")

        # Read per-camera hardware
        camera_data = {}
        for camera_index, controller in self.cameras.items():
            with self._state_lock:
                cam_state = self._state.get_camera(camera_index)
                cam_connected = cam_state.connected
                cam_saving = cam_state.is_saving

            if cam_connected:
                try:
                    camera_data[camera_index] = {
                        'exposure': controller.get_exposure(),
                        'temperature': controller.get_property('SENSOR_TEMPERATURE'),
                        'fps': controller.get_frame_rate(),
                        'params': controller.get_all_params(),
                    }
                except Exception as e:
                    logger.error(f"Error reading camera {camera_index} status: {e}")

            # Read save thread stats
            if cam_saving and camera_index in self._save_threads:
                save_thread = self._save_threads[camera_index]
                if save_thread:
                    camera_data.setdefault(camera_index, {})
                    camera_data[camera_index]['frames_saved'] = save_thread.total_frames_saved
                    camera_data[camera_index]['frames_dropped'] = save_thread.total_frames_dropped
                    camera_data[camera_index]['cubes_saved'] = save_thread.cubes_written

        # Update state with lock (fast - just assignments)
        with self._state_lock:
            # Update per-camera state
            for camera_index, data in camera_data.items():
                cam_state = self._state.get_camera(camera_index)
                if 'exposure' in data:
                    cam_state.exposure = data['exposure']
                if 'temperature' in data and data['temperature']:
                    cam_state.temperature = data['temperature']
                if 'fps' in data and data['fps'] is not None:
                    cam_state.frame_rate = data['fps']
                if 'params' in data and data['params']:
                    cam_state.params = data['params']
                if 'frames_saved' in data:
                    cam_state.frames_saved = data['frames_saved']
                if 'frames_dropped' in data:
                    cam_state.frames_dropped = data['frames_dropped']
                if 'cubes_saved' in data:
                    cam_state.cubes_saved = data['cubes_saved']

            # Update shared telescope state
            if telescope_connected:
                self._state.telescope_focus = telescope_focus
                if telescope_pos:
                    self._state.telescope_ra = telescope_pos.ra
                    self._state.telescope_dec = telescope_pos.dec
                    self._state.telescope_ha = telescope_pos.ha
                    self._state.telescope_lst = telescope_pos.lst
                    self._state.telescope_airmass = telescope_pos.airmass
                    self._state.telescope_utc = f"{telescope_pos.utc_day} {telescope_pos.utc_time}"
                    self._state.telescope_utc_day = telescope_pos.utc_day
                    self._state.telescope_utc_time = telescope_pos.utc_time
                if telescope_status:
                    self._state.telescope_tube_length_mm = telescope_status.tube_length_mm
                    self._state.telescope_offset_ra_arcsec = telescope_status.offset_ra_arcsec
                    self._state.telescope_offset_dec_arcsec = telescope_status.offset_dec_arcsec
                    self._state.telescope_rate_ra_arcsec_hr = telescope_status.rate_ra_arcsec_hr
                    self._state.telescope_rate_dec_arcsec_hr = telescope_status.rate_dec_arcsec_hr
                    self._state.telescope_cass_ring_angle = telescope_status.cass_ring_angle
                    self._state.telescope_id = telescope_status.telescope_id

            if filterwheel_connected:
                self._state.current_filter = current_filter

        self._notify_status_change()

    # ==========================================================================
    # Private Methods
    # ==========================================================================

    def _get_telescope_data_for_cube(self):
        """
        Query fresh telescope and filter data for a cube (called at first frame).

        This is used as a callback by the writer to get telescope data
        at the exact moment the first frame of each cube arrives,
        ensuring accurate telescope position/status/filter for that cube.

        Returns:
            tuple: (position_dict, status_dict, filter_name) or (None, None, None) if unavailable
        """
        position_dict = None
        status_dict = None
        filter_name = None

        # Query telescope if connected
        if self._state.telescope_connected:
            try:
                # Query telescope NOW (using persistent socket connection)
                position = self.telescope.get_position()
                status = self.telescope.get_status()

                # Convert dataclasses to dicts
                if position:
                    position_dict = {
                        'ra': position.ra,
                        'dec': position.dec,
                        'ha': position.ha,
                        'lst': position.lst,
                        'airmass': position.airmass,
                        'utc_time': position.utc_time,
                        'utc_day': position.utc_day,
                    }

                if status:
                    status_dict = {
                        'focus_mm': status.focus_mm,
                        'tube_length_mm': status.tube_length_mm,
                        'offset_ra_arcsec': status.offset_ra_arcsec,
                        'offset_dec_arcsec': status.offset_dec_arcsec,
                        'rate_ra_arcsec_hr': status.rate_ra_arcsec_hr,
                        'rate_dec_arcsec_hr': status.rate_dec_arcsec_hr,
                        'cass_ring_angle': status.cass_ring_angle,
                        'telescope_id': status.telescope_id,
                        'utc_time': status.utc_time,
                        'utc_day': status.utc_day,
                    }

            except Exception as e:
                logger.warning(f"Could not query telescope data for cube: {e}")

        # Query current filter if filterwheel connected
        if self._state.filterwheel_connected and self.filterwheel:
            try:
                filter_name = self.filterwheel.filter
            except Exception as e:
                logger.warning(f"Could not query filter for cube: {e}")

        return (position_dict, status_dict, filter_name)

    def _on_camera_frame(self, frame: np.ndarray, timestamp: float, framestamp: int, camera_index: int = 0):
        """Internal handler for camera frames."""
        with self._state_lock:
            cam_state = self._state.get_camera(camera_index)
            cam_state.frames_captured += 1
            frames_captured = cam_state.frames_captured
            is_saving = cam_state.is_saving

        # Log periodically
        if frames_captured % 100 == 1:
            logger.info(f"Camera {camera_index}: received frame {frames_captured}, is_saving={is_saving}")

        # Note: Frames are sent to save_queue directly by camera controller
        # No need to forward to writer here

        # Update per-camera display queue (keep only latest)
        display_queue = self._display_queues.get(camera_index, self._display_queue)
        try:
            while not display_queue.empty():
                try:
                    display_queue.get_nowait()
                except:
                    break
            display_queue.put_nowait(frame)
        except queue.Full:
            pass

        # Forward to external callbacks
        with self._callback_lock:
            for callback in self._frame_callbacks:
                try:
                    callback(frame, timestamp, framestamp)
                except Exception as e:
                    logger.error(f"Error in frame callback: {e}")

    def _notify_status_change(self):
        """Notify status callbacks of state change."""
        state_copy = self.state

        with self._callback_lock:
            for callback in self._status_callbacks:
                try:
                    callback(state_copy)
                except Exception as e:
                    logger.error(f"Error in status callback: {e}")

    # ==========================================================================
    # Context Manager
    # ==========================================================================

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup all connections."""
        logger.info("Cleaning up Cerberus API...")

        if self._state.is_saving:
            self.stop_saving()

        if self._state.camera_streaming:
            self.stop_streaming()

        if self._state.camera_connected:
            self.disconnect_camera()

        if self._state.telescope_connected:
            self.disconnect_telescope()

        if self._state.filterwheel_connected:
            self.disconnect_filterwheel()

        return False
