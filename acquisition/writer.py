# acquisition/writer.py
"""FITS data cube writer for high-speed camera data."""

import os
import gc
import time
import queue
import logging
import warnings
import threading
import numpy as np
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from astropy.io import fits

logger = logging.getLogger(__name__)


def _get_acquisition_config():
    """Lazy load acquisition config to avoid circular imports."""
    try:
        from ..config import get_config
        return get_config().acquisition
    except Exception:
        return None


class FITSWriter:
    """
    Handles saving frames to FITS data cubes.

    Receives frames via add_frame() and batches them into FITS cubes
    with configurable size. Uses a thread pool for parallel disk writes
    to maximize throughput.

    Example usage:
        writer = FITSWriter()
        writer.configure(
            output_dir="/data/tonight",
            object_name="M31",
            frames_per_cube=1000
        )
        writer.start()

        # Add frames (typically from camera callback)
        for frame, ts, fs in frames:
            writer.add_frame(frame, ts, fs)

        writer.stop()  # Flushes remaining frames
    """

    def __init__(self, max_queue_size: int = None, max_pending_writes: int = None):
        """
        Initialize FITS writer.

        Args:
            max_queue_size: Maximum frames to queue before backpressure (uses config if None)
            max_pending_writes: Maximum concurrent write operations (uses config if None)
        """
        # Load from config if available
        config = _get_acquisition_config()
        if config:
            max_queue_size = max_queue_size or config.max_queue_size
            max_pending_writes = max_pending_writes or config.max_pending_writes
            self._frames_per_cube = config.frames_per_cube
            self._thread_pool_workers = config.thread_pool_workers
            self._backpressure_threshold = config.backpressure_threshold
        else:
            max_queue_size = max_queue_size or 10000
            max_pending_writes = max_pending_writes or 6
            self._frames_per_cube = 1000
            self._thread_pool_workers = 8
            self._backpressure_threshold = 0.9

        self._frame_queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._write_thread: Optional[threading.Thread] = None
        self._running = False

        # Configuration
        self._output_dir = os.getcwd()
        self._object_name = "unknown"
        self._max_pending_writes = max_pending_writes

        # State
        self._cube_index = 0
        self._save_folder = ""
        self._start_time_str = ""

        # Preallocated buffers
        self._frame_buffer: Optional[np.ndarray] = None
        self._timestamp_buffer: Optional[np.ndarray] = None
        self._framestamp_buffer: Optional[np.ndarray] = None
        self._buffer_index = 0

        # Statistics
        self._total_frames_saved = 0
        self._total_frames_dropped = 0
        self._frames_since_report = 0
        self._last_report_time = time.time()

        # Thread pool for parallel writes
        self._executor: Optional[ThreadPoolExecutor] = None
        self._pending_writes: list = []

        # First frame tracking
        self._first_frame_timestamp: Optional[float] = None

        # Timing info for FITS headers
        self._timing_info: Dict[str, Any] = {}

        # Camera params snapshot (set by caller)
        self._camera_params: Dict[str, Any] = {}
        self._params_lock = threading.Lock()

        # Telescope params snapshot (set by caller)
        self._telescope_position: Optional[Dict[str, Any]] = None
        self._telescope_status: Optional[Dict[str, Any]] = None

        # Telescope data callback for querying on first frame of each cube
        self._telescope_data_callback = None

        # Per-cube telescope data (queried when first frame of cube arrives)
        self._cube_telescope_data: Dict[int, tuple] = {}  # cube_index -> (position, status)

    def configure(
        self,
        output_dir: str,
        object_name: str,
        frames_per_cube: int = 1000,
        timing_info: Optional[Dict[str, Any]] = None
    ):
        """
        Configure the writer.

        Args:
            output_dir: Base directory for output files
            object_name: Object name for FITS headers and filenames
            frames_per_cube: Number of frames per FITS cube
            timing_info: Timing information for FITS headers
        """
        self._output_dir = output_dir
        self._object_name = object_name
        self._frames_per_cube = frames_per_cube
        self._timing_info = timing_info or {}

    def set_camera_params(self, params: Dict[str, Any]):
        """Set camera parameters for FITS headers."""
        with self._params_lock:
            self._camera_params = dict(params)

    def set_telescope_data(self, position: Optional[Dict[str, Any]] = None,
                           status: Optional[Dict[str, Any]] = None):
        """
        Set telescope data for FITS headers.

        Args:
            position: Dict from TelescopePosition (ra, dec, ha, airmass, etc.)
            status: Dict from TelescopeStatus (focus_mm, offsets, etc.)
        """
        with self._params_lock:
            self._telescope_position = position
            self._telescope_status = status

    def set_telescope_callback(self, callback):
        """
        Set callback for querying telescope data on-demand.

        The callback should return (position_dict, status_dict) when called.
        This will be called when the first frame of each cube arrives,
        ensuring telescope data matches the time of the first exposure.

        Args:
            callback: Callable that returns (position_dict, status_dict)
        """
        self._telescope_data_callback = callback

    def start(self):
        """Start the writer thread."""
        if self._running:
            logger.warning("Writer already running")
            return

        logger.info("Starting FITS writer")
        self._running = True
        self._cube_index = 0
        self._total_frames_saved = 0
        self._total_frames_dropped = 0
        self._first_frame_timestamp = None

        # Reset buffers
        self._frame_buffer = None
        self._timestamp_buffer = None
        self._framestamp_buffer = None
        self._buffer_index = 0

        # Create save folder
        self._start_time_str = time.strftime('%Y%m%d_%H%M%S')
        date_str = time.strftime('%Y_%m_%d')
        self._save_folder = os.path.join(self._output_dir, f"captures_{date_str}")
        os.makedirs(self._save_folder, exist_ok=True)
        logger.info(f"Saving to: {self._save_folder}")

        # Start thread pool
        self._executor = ThreadPoolExecutor(max_workers=self._thread_pool_workers, thread_name_prefix="FITSWriter")
        self._pending_writes = []

        # Start write thread
        self._write_thread = threading.Thread(
            target=self._write_loop,
            name="FITSWriteLoop",
            daemon=True
        )
        self._write_thread.start()

    def stop(self):
        """Stop the writer and flush remaining frames."""
        if not self._running:
            return

        logger.info("Stopping FITS writer")
        self._running = False

        # Wait for write thread to finish
        if self._write_thread is not None:
            self._write_thread.join(timeout=30.0)
            self._write_thread = None

        # Shutdown thread pool
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

        logger.info(f"Writer stopped. Saved: {self._total_frames_saved}, dropped: {self._total_frames_dropped}")

    def add_frame(self, frame: np.ndarray, timestamp: float, framestamp: int):
        """
        Add a frame to the save queue.

        Args:
            frame: Image data as numpy array
            timestamp: Camera timestamp in seconds
            framestamp: Frame counter from camera
        """
        if not self._running:
            return

        queue_size = self._frame_queue.qsize()
        max_size = self._frame_queue.maxsize

        # Backpressure: drop frames if queue is getting full
        if queue_size > max_size * self._backpressure_threshold:
            self._total_frames_dropped += 1
            if self._total_frames_dropped % 100 == 1:
                logger.error(f"Queue full ({queue_size}/{max_size}), dropping frames")
            return

        try:
            self._frame_queue.put_nowait((frame, timestamp, framestamp))
        except queue.Full:
            self._total_frames_dropped += 1

    @property
    def is_running(self) -> bool:
        """Check if writer is running."""
        return self._running

    @property
    def frames_written(self) -> int:
        """Total frames written to disk."""
        return self._total_frames_saved

    @property
    def cubes_written(self) -> int:
        """Total cubes written to disk."""
        return self._cube_index

    @property
    def frames_dropped(self) -> int:
        """Total frames dropped due to backpressure."""
        return self._total_frames_dropped

    @property
    def queue_size(self) -> int:
        """Current queue size."""
        return self._frame_queue.qsize()

    # === Private Methods ===

    def _write_loop(self):
        """Main write loop (runs in separate thread)."""
        try:
            while self._running or not self._frame_queue.empty():
                try:
                    # Wait for pending writes if at limit
                    while len(self._pending_writes) >= self._max_pending_writes:
                        self._check_pending_writes()
                        time.sleep(0.001)

                    # Read frames from queue
                    frames_read = 0
                    max_batch = 100

                    while frames_read < max_batch and self._buffer_index < self._frames_per_cube:
                        try:
                            frame, timestamp, framestamp = self._frame_queue.get(timeout=0.001)

                            # Track first frame ever
                            if self._first_frame_timestamp is None:
                                self._first_frame_timestamp = timestamp
                                self._timing_info['first_frame_timestamp'] = timestamp
                                self._timing_info['time_first_frame_arrived'] = time.time()
                                logger.info(f"First frame arrived: camera time {timestamp:.6f}s")

                            # Query telescope data on first frame of each NEW cube
                            if self._buffer_index == 0 and self._telescope_data_callback:
                                try:
                                    next_cube_idx = self._cube_index + 1
                                    pos, status = self._telescope_data_callback()
                                    self._cube_telescope_data[next_cube_idx] = (pos, status)
                                    logger.debug(f"Queried telescope data for cube {next_cube_idx} at first frame")
                                except Exception as e:
                                    logger.warning(f"Failed to query telescope data for cube: {e}")
                                    self._cube_telescope_data[next_cube_idx] = (None, None)

                            # Lazy buffer allocation
                            if self._frame_buffer is None:
                                self._allocate_buffers(frame.shape, frame.dtype)

                            # Copy to buffer
                            self._frame_buffer[self._buffer_index] = frame
                            self._timestamp_buffer[self._buffer_index] = timestamp
                            self._framestamp_buffer[self._buffer_index] = framestamp
                            self._buffer_index += 1
                            frames_read += 1
                            self._frames_since_report += 1

                        except queue.Empty:
                            break

                    # Write cube when buffer is full
                    if self._buffer_index >= self._frames_per_cube:
                        self._write_cube_async()

                    # Check pending writes
                    self._check_pending_writes()

                    # Report progress
                    self._report_progress()

                except Exception as e:
                    logger.error(f"Write loop error: {e}", exc_info=True)

            # Write remaining frames
            if self._buffer_index > 0:
                logger.info(f"Writing final cube with {self._buffer_index} frames")
                self._write_cube_async(partial=True)

            # Wait for pending writes
            logger.info(f"Waiting for {len(self._pending_writes)} pending writes...")
            self._wait_for_pending_writes()

        except Exception as e:
            logger.error(f"Fatal write loop error: {e}", exc_info=True)

    def _allocate_buffers(self, frame_shape: tuple, frame_dtype: np.dtype):
        """Allocate buffers for frame batching."""
        frame_size_mb = (frame_shape[0] * frame_shape[1] * np.dtype(frame_dtype).itemsize) / (1024**2)
        total_size_mb = frame_size_mb * self._frames_per_cube
        logger.info(f"Allocating buffer for {self._frames_per_cube} frames ({total_size_mb:.0f} MB)")

        self._frame_buffer = np.empty((self._frames_per_cube, *frame_shape), dtype=frame_dtype)
        self._timestamp_buffer = np.empty(self._frames_per_cube, dtype=np.float64)
        self._framestamp_buffer = np.empty(self._frames_per_cube, dtype=np.int64)
        self._buffer_index = 0

    def _write_cube_async(self, partial: bool = False):
        """Queue a cube for asynchronous writing."""
        self._cube_index += 1
        n_frames = self._buffer_index

        filename = f"{self._object_name}_{self._start_time_str}_cube{self._cube_index:03d}.fits"
        filepath = os.path.join(self._save_folder, filename)
        logger.info(f"Queuing cube {self._cube_index} ({n_frames} frames)")

        # Prepare data
        if partial:
            frames = self._frame_buffer[:n_frames].copy()
            timestamps = self._timestamp_buffer[:n_frames].copy()
            framestamps = self._framestamp_buffer[:n_frames].copy()
        else:
            # Zero-copy: transfer ownership
            frames = self._frame_buffer
            timestamps = self._timestamp_buffer
            framestamps = self._framestamp_buffer

            # Allocate new buffers
            self._frame_buffer = np.empty_like(frames)
            self._timestamp_buffer = np.empty_like(timestamps)
            self._framestamp_buffer = np.empty_like(framestamps)

        # Get camera params
        with self._params_lock:
            camera_params = dict(self._camera_params)

        # Get telescope data for THIS specific cube (queried at first frame)
        # Fall back to global cached data if per-cube data not available
        if self._cube_index in self._cube_telescope_data:
            telescope_position, telescope_status = self._cube_telescope_data[self._cube_index]
            logger.debug(f"Using per-cube telescope data for cube {self._cube_index}")
        else:
            # Fallback to global cached data (old behavior)
            with self._params_lock:
                telescope_position = dict(self._telescope_position) if self._telescope_position else None
                telescope_status = dict(self._telescope_status) if self._telescope_status else None
            logger.debug(f"Using cached telescope data for cube {self._cube_index}")

        # Submit to thread pool
        write_start = time.time()
        future = self._executor.submit(
            self._write_fits,
            filepath, frames, timestamps, framestamps,
            n_frames, self._cube_index, camera_params,
            telescope_position, telescope_status, write_start
        )

        # Track cube index for cleanup after write completes
        self._pending_writes.append((filepath, future, n_frames, self._cube_index))
        self._total_frames_saved += n_frames
        self._buffer_index = 0

        gc.collect()

    def _write_fits(
        self,
        filepath: str,
        frames: np.ndarray,
        timestamps: np.ndarray,
        framestamps: np.ndarray,
        n_frames: int,
        cube_index: int,
        camera_params: Dict[str, Any],
        telescope_position: Optional[Dict[str, Any]],
        telescope_status: Optional[Dict[str, Any]],
        write_start: float
    ) -> bool:
        """Write FITS file (runs in thread pool)."""
        try:
            start_time = time.time()

            # Create primary HDU with headers
            primary_hdu = fits.PrimaryHDU()
            primary_hdu.header['OBJECT'] = (self._object_name, 'Object name')
            primary_hdu.header['DATE-OBS'] = (datetime.now(timezone.utc).isoformat(), 'UTC date')
            primary_hdu.header['CUBEIDX'] = (cube_index, 'Cube index')
            primary_hdu.header['NFRAMES'] = (n_frames, 'Frames in cube')

            # Add timing info
            self._add_timing_headers(primary_hdu.header)

            # Add telescope data to primary header
            self._add_telescope_headers(primary_hdu.header, telescope_position, telescope_status)

            # Create image HDU
            data_cube = frames[:n_frames] if len(frames) > n_frames else frames
            image_hdu = fits.ImageHDU(data=data_cube)
            image_hdu.header['EXTNAME'] = 'DATA_CUBE'

            # Add camera parameters using HIERARCH for long keywords
            self._add_camera_headers(image_hdu.header, camera_params)

            # Create timestamp table
            ts_data = timestamps[:n_frames] if len(timestamps) > n_frames else timestamps
            fs_data = framestamps[:n_frames] if len(framestamps) > n_frames else framestamps

            col1 = fits.Column(name='TIMESTAMP', format='D', array=ts_data)
            col2 = fits.Column(name='FRAMESTAMP', format='K', array=fs_data)
            timestamp_hdu = fits.BinTableHDU.from_columns([col1, col2])
            timestamp_hdu.header['EXTNAME'] = 'TIMESTAMPS'
            timestamp_hdu.header['TUNIT1'] = ('seconds', 'Camera timestamp')
            timestamp_hdu.header['TUNIT2'] = ('count', 'Frame counter')

            # Write file
            hdulist = fits.HDUList([primary_hdu, image_hdu, timestamp_hdu])
            hdulist.writeto(filepath, overwrite=True)
            hdulist.close()

            # Log performance
            write_time = time.time() - start_time
            queue_wait = start_time - write_start
            file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
            speed_mbps = file_size_mb / write_time if write_time > 0 else 0

            logger.info(
                f"Wrote {os.path.basename(filepath)} ({file_size_mb:.1f} MB) "
                f"in {write_time:.2f}s ({speed_mbps:.1f} MB/s)"
            )

            del data_cube, hdulist
            return True

        except Exception as e:
            logger.error(f"FITS write error: {e}", exc_info=True)
            return False

    def _add_camera_headers(self, header, camera_params: Dict[str, Any]):
        """
        Add camera parameters to FITS header using HIERARCH for long keywords.

        HIERARCH allows keywords longer than 8 characters, preventing truncation
        and accidental overwriting of similarly-named parameters.
        """
        for key, value in camera_params.items():
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore")
                try:
                    if len(key) > 8:
                        # Use HIERARCH convention for long keywords
                        # Format: "HIERARCH CAM KEYWORD_NAME"
                        hierarch_key = f"HIERARCH CAM {key}"
                        header[hierarch_key] = value
                    else:
                        header[key] = value
                except Exception as e:
                    logger.debug(f"Could not add header {key}: {e}")

    def _add_telescope_headers(self, header,
                                position: Optional[Dict[str, Any]],
                                status: Optional[Dict[str, Any]]):
        """Add telescope parameters to FITS header."""
        if position:
            # From TelescopePosition
            header['TELRA'] = (position.get('ra', 'N/A'), 'Right Ascension')
            header['TELDEC'] = (position.get('dec', 'N/A'), 'Declination')
            header['TELHA'] = (position.get('ha', 'N/A'), 'Hour Angle')
            header['TELLST'] = (position.get('lst', 'N/A'), 'Local Sidereal Time')
            header['AIRMASS'] = (position.get('airmass', 'N/A'), 'Airmass')
            header['TELUTC'] = (position.get('utc_time', 'N/A'), 'UTC time from TCS')
            header['TELDAY'] = (position.get('utc_day', 'N/A'), 'UTC day number')

        if status:
            # From TelescopeStatus
            header['TELFOCUS'] = (status.get('focus_mm', 'N/A'), 'Focus position (mm)')
            header['TUBELEN'] = (status.get('tube_length_mm', 'N/A'), 'Tube length (mm)')
            header['HIERARCH TEL OFFSET_RA'] = (status.get('offset_ra_arcsec', 'N/A'), 'RA offset (arcsec)')
            header['HIERARCH TEL OFFSET_DEC'] = (status.get('offset_dec_arcsec', 'N/A'), 'Dec offset (arcsec)')
            header['HIERARCH TEL RATE_RA'] = (status.get('rate_ra_arcsec_hr', 'N/A'), 'RA rate (arcsec/hr)')
            header['HIERARCH TEL RATE_DEC'] = (status.get('rate_dec_arcsec_hr', 'N/A'), 'Dec rate (arcsec/hr)')
            header['CASSRING'] = (status.get('cass_ring_angle', 'N/A'), 'Cass ring angle (deg)')
            header['TELID'] = (status.get('telescope_id', 'N/A'), 'Telescope ID')

    def _add_timing_headers(self, header):
        """Add timing information to FITS header."""
        info = self._timing_info

        if 'time_before_cap_start' in info:
            header['T_BFCAPS'] = (info['time_before_cap_start'], 'Before cap_start (Unix)')
            header['BFCAPISO'] = (
                datetime.fromtimestamp(info['time_before_cap_start'], tz=timezone.utc).isoformat(),
                'Before cap_start (ISO UTC)'
            )

        if 'time_after_cap_start' in info:
            header['T_AFCAPS'] = (info['time_after_cap_start'], 'After cap_start (Unix)')
            header['AFCAPISO'] = (
                datetime.fromtimestamp(info['time_after_cap_start'], tz=timezone.utc).isoformat(),
                'After cap_start (ISO UTC)'
            )

        if 'time_first_frame_arrived' in info:
            header['T_FRAME1'] = (info['time_first_frame_arrived'], 'First frame (Unix)')
            header['FR1ISO'] = (
                datetime.fromtimestamp(info['time_first_frame_arrived'], tz=timezone.utc).isoformat(),
                'First frame (ISO UTC)'
            )

        if 'first_frame_timestamp' in info:
            header['CAMTS1'] = (info['first_frame_timestamp'], 'Camera timestamp (s)')

        if 'exposure_time' in info:
            header['EXPTIME'] = (info['exposure_time'], 'Exposure time (s)')

        if 'readout_time' in info:
            header['READTIME'] = (info['readout_time'], 'Readout time (s)')

        if 'trigger_source' in info:
            header['TRIGSRC'] = (info['trigger_source'], 'Trigger source')

    def _check_pending_writes(self):
        """Check status of pending writes."""
        completed = []
        for filepath, future, n_frames, cube_idx in self._pending_writes:
            if future.done():
                try:
                    if not future.result(timeout=0):
                        logger.error(f"Write failed: {filepath}")
                        self._total_frames_dropped += n_frames
                except Exception as e:
                    logger.error(f"Write error: {e}")
                    self._total_frames_dropped += n_frames

                # Clean up per-cube telescope data after write completes
                if cube_idx in self._cube_telescope_data:
                    del self._cube_telescope_data[cube_idx]

                completed.append((filepath, future, n_frames, cube_idx))

        for item in completed:
            self._pending_writes.remove(item)

        if completed:
            gc.collect()

    def _wait_for_pending_writes(self):
        """Wait for all pending writes to complete."""
        for i, (filepath, future, n_frames, cube_idx) in enumerate(self._pending_writes, 1):
            try:
                logger.info(f"Waiting for write {i}/{len(self._pending_writes)}")
                if not future.result(timeout=60):
                    logger.error(f"Final write failed: {filepath}")
                    self._total_frames_dropped += n_frames
            except Exception as e:
                logger.error(f"Final write error: {e}")
                self._total_frames_dropped += n_frames

            # Clean up per-cube telescope data
            if cube_idx in self._cube_telescope_data:
                del self._cube_telescope_data[cube_idx]

        self._pending_writes.clear()
        self._cube_telescope_data.clear()  # Final cleanup

    def _report_progress(self):
        """Report save progress periodically."""
        report_interval = max(self._frames_per_cube // 5, 1)

        if self._frames_since_report >= report_interval:
            elapsed = time.time() - self._last_report_time
            fps = self._frames_since_report / elapsed if elapsed > 0 else 0
            queue_size = self._frame_queue.qsize()
            queue_pct = 100.0 * queue_size / self._frame_queue.maxsize

            logger.info(
                f"Writer: {fps:.1f} fps, queue: {queue_size} ({queue_pct:.1f}%), "
                f"pending: {len(self._pending_writes)}, "
                f"saved: {self._total_frames_saved}, dropped: {self._total_frames_dropped}"
            )

            self._frames_since_report = 0
            self._last_report_time = time.time()
