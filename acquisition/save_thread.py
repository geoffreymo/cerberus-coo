# acquisition/save_thread.py
"""
Save thread using ThreadPoolExecutor for parallel FITS writing.

Architecture:
- Queue input from camera thread
- ThreadPoolExecutor for parallel FITS writing
- Simple numpy array buffering (no shared memory needed for threads)
"""

import os
import logging
import time
import queue
import threading
import traceback
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from astropy.io import fits
from astropy.io.fits.verify import VerifyWarning
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

# Suppress HIERARCH keyword warnings and card truncation warnings
warnings.filterwarnings('ignore', category=VerifyWarning, message='.*HIERARCH.*')
warnings.filterwarnings('ignore', category=VerifyWarning, message='.*Card is too long.*')


def write_fits_cube(
    frames: np.ndarray,
    timestamps: np.ndarray,
    framestamps: np.ndarray,
    filepath: str,
    header_dict: Dict[str, Any],
    object_name: str,
    cube_index: int,
    camera_params: Dict[str, Any],
    filter_name: Optional[str] = None
) -> tuple:
    """
    Write FITS file from numpy arrays - runs in thread pool.

    Arrays are already copied, so this function owns the data.
    """
    try:
        write_start = time.time()
        num_frames = len(timestamps)

        # Build FITS header
        primary_hdr = fits.Header()
        primary_hdr['OBJECT'] = (object_name, 'Object name')
        primary_hdr['CUBEIDX'] = (cube_index, 'Cube index number')
        primary_hdr['NFRAMES'] = (num_frames, 'Number of frames in cube')

        # UTC timestamp
        utc_now = datetime.now(timezone.utc)
        primary_hdr['DATE-OBS'] = (utc_now.isoformat(), 'UTC at file write')

        # Filter name
        if filter_name:
            primary_hdr['FILTER'] = (filter_name, 'Filter name')

        # Additional header items
        for key, value in header_dict.items():
            try:
                primary_hdr[key] = value
            except:
                pass

        # Primary HDU (header only)
        primary_hdu = fits.PrimaryHDU(header=primary_hdr)

        # ImageHDU for data cube
        image_hdu = fits.ImageHDU(data=frames)
        image_hdu.header['EXTNAME'] = 'DATA_CUBE'

        # Add camera parameters
        for key, value in camera_params.items():
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore")
                    image_hdu.header[key[:8]] = value
            except:
                pass

        # BinTableHDU for timestamps
        col1 = fits.Column(name='TIMESTAMP', format='D', array=timestamps)
        col2 = fits.Column(name='FRAMESTAMP', format='K', array=framestamps)
        timestamp_hdu = fits.BinTableHDU.from_columns([col1, col2])
        timestamp_hdu.header['EXTNAME'] = 'TIMESTAMPS'

        hdul = fits.HDUList([primary_hdu, image_hdu, timestamp_hdu])
        hdul.writeto(filepath, overwrite=True)

        write_time = time.time() - write_start
        file_size_mb = os.path.getsize(filepath) / 1e6

        return (True, filepath, write_time, num_frames, file_size_mb)

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        traceback.print_exc()
        return (False, filepath, 0, 0, error_msg)


class OptimizedSaveThread(threading.Thread):
    """
    Save thread: Queue input + ThreadPoolExecutor

    Architecture:
    - Camera thread: np.copy() + queue.put()
    - Save thread: accumulates frames into buffer, copies & submits when full
    - ThreadPoolExecutor: writes FITS files in parallel threads

    Note: We use ThreadPoolExecutor (not ProcessPoolExecutor) because fork()
    corrupts DCAM's internal buffer state.
    """

    NUM_WRITERS = 2  # Number of parallel write threads (reduced to minimize GIL contention)

    def __init__(
        self,
        save_queue: queue.Queue,
        output_dir: str,
        object_name: str,
        header_dict: Optional[Dict[str, Any]] = None,
        frames_per_cube: int = 100,
        camera_params: Optional[Dict[str, Any]] = None,
        filter_callback: Optional[Callable] = None
    ):
        super().__init__(name="SaveThread", daemon=True)

        self.save_queue = save_queue
        self.running = True
        self.output_dir = output_dir
        self.object_name = object_name
        self.header_dict = header_dict or {}
        self.frames_per_cube = frames_per_cube
        self.camera_params = camera_params or {}
        self.filter_callback = filter_callback
        self.cube_index = 0

        # Current accumulation buffer (allocated on first frame)
        self.frame_buffer = None
        self.ts_buffer = None
        self.fs_buffer = None
        self.current_frame_idx = 0

        # Thread pool for parallel writes
        self.executor = ThreadPoolExecutor(max_workers=self.NUM_WRITERS)
        self.pending_writes = []

        # Statistics
        self.total_frames_saved = 0
        self.total_frames_dropped = 0
        self.cubes_written = 0

        # Timing stats
        self.timing_stats = defaultdict(list)
        self.last_timing_report = time.time()

    def run(self):
        """Main save thread loop"""
        try:
            logger.info(f"Save thread started: {self.frames_per_cube} frames/cube, "
                       f"{self.NUM_WRITERS} writer threads")
            logger.info(f"Saving to: {self.output_dir}")

            self.session_timestamp = time.strftime('%Y%m%d_%H%M%S')
            os.makedirs(self.output_dir, exist_ok=True)

            while self.running or not self.save_queue.empty():
                try:
                    t0 = time.perf_counter()

                    try:
                        frame, timestamp, framestamp = self.save_queue.get(timeout=0.001)

                        # Initialize buffer on first frame
                        if self.frame_buffer is None:
                            self._allocate_buffer(frame.shape, frame.dtype)

                        # Add frame to buffer
                        if self.current_frame_idx < self.frames_per_cube:
                            self.frame_buffer[self.current_frame_idx] = frame
                            self.ts_buffer[self.current_frame_idx] = timestamp
                            self.fs_buffer[self.current_frame_idx] = framestamp
                            self.current_frame_idx += 1

                        self.timing_stats['frame_copy'].append(time.perf_counter() - t0)

                    except queue.Empty:
                        pass

                    # Write cube when buffer is full
                    if self.current_frame_idx >= self.frames_per_cube:
                        self._write_cube_async()

                    # Check pending writes (non-blocking)
                    self._check_pending_writes()

                    # Report timing periodically
                    if time.time() - self.last_timing_report > 10.0:
                        self._report_timing()
                        self.last_timing_report = time.time()

                except Exception as e:
                    logger.error(f"Save thread error: {e}")
                    traceback.print_exc()

            # Write remaining frames
            if self.current_frame_idx > 0:
                logger.info(f"Writing final cube with {self.current_frame_idx} frames")
                self._write_cube_async()

            # Wait for all pending writes
            logger.info(f"Waiting for {len(self.pending_writes)} pending writes...")
            self._wait_for_pending_writes()

        except Exception as e:
            logger.error(f"Fatal save thread error: {e}")
            traceback.print_exc()
        finally:
            self.cleanup()

    def _allocate_buffer(self, frame_shape, frame_dtype):
        """Allocate accumulation buffer"""
        buffer_shape = (self.frames_per_cube,) + frame_shape
        self.frame_buffer = np.empty(buffer_shape, dtype=frame_dtype)
        self.ts_buffer = np.empty(self.frames_per_cube, dtype=np.float64)
        self.fs_buffer = np.empty(self.frames_per_cube, dtype=np.int64)

        buffer_mb = self.frame_buffer.nbytes / 1e6
        logger.info(f"Allocated save buffer: {self.frames_per_cube} x {frame_shape} "
                   f"({buffer_mb:.0f} MB)")

    def _write_cube_async(self):
        """Copy buffer and submit write to thread pool"""
        try:
            self.cube_index += 1
            num_frames = self.current_frame_idx

            # Generate filename
            filename = f"{self.session_timestamp}_{self.object_name}_cube{self.cube_index:03d}.fits"
            filepath = os.path.join(self.output_dir, filename)

            # COPY the data before submitting - this ensures the write thread
            # has its own copy and we can reuse the buffer immediately
            frames_copy = self.frame_buffer[:num_frames].copy()
            ts_copy = self.ts_buffer[:num_frames].copy()
            fs_copy = self.fs_buffer[:num_frames].copy()

            # Get current filter
            filter_name = None
            if self.filter_callback:
                try:
                    filter_name = self.filter_callback()
                except:
                    pass

            logger.info(f"Queuing cube {self.cube_index} ({num_frames} frames). "
                       f"Pending: {len(self.pending_writes)}")

            # Submit to thread pool
            future = self.executor.submit(
                write_fits_cube,
                frames_copy, ts_copy, fs_copy,
                filepath, dict(self.header_dict), self.object_name,
                self.cube_index, dict(self.camera_params), filter_name
            )

            self.pending_writes.append((filepath, future, time.time()))

            # Reset buffer index for next cube
            self.current_frame_idx = 0

        except Exception as e:
            logger.error(f"Write cube error: {e}")
            traceback.print_exc()

    def _check_pending_writes(self):
        """Check status of pending writes (non-blocking)"""
        completed = []

        for item in self.pending_writes:
            filepath, future, start_time = item
            if future.done():
                try:
                    result = future.result(timeout=0)

                    if isinstance(result, tuple) and len(result) >= 4:
                        success, fpath, write_time, nframes, *rest = result
                        if success:
                            file_size = rest[0] if rest else 0
                            throughput = file_size / write_time if write_time > 0 else 0
                            logger.info(f"Wrote {os.path.basename(fpath)} "
                                       f"({nframes} frames, {file_size:.1f} MB) "
                                       f"in {write_time:.2f}s ({throughput:.1f} MB/s)")
                            self.total_frames_saved += nframes
                            self.cubes_written += 1
                        else:
                            error_msg = rest[0] if rest else "Unknown error"
                            logger.error(f"Failed to write {fpath}: {error_msg}")

                    completed.append(item)

                except Exception as e:
                    logger.error(f"Error checking write result: {e}")
                    completed.append(item)

        for item in completed:
            self.pending_writes.remove(item)

    def _wait_for_pending_writes(self):
        """Wait for all pending writes to complete"""
        for filepath, future, start_time in self.pending_writes:
            try:
                result = future.result(timeout=60)
                if isinstance(result, tuple) and result[0]:
                    logger.info(f"Completed: {os.path.basename(filepath)}")
                    self.total_frames_saved += result[3]
                    self.cubes_written += 1
            except Exception as e:
                logger.error(f"Error waiting for write: {e}")

        self.pending_writes.clear()

    def _report_timing(self):
        """Report timing statistics"""
        if self.timing_stats['frame_copy']:
            avg_copy = np.mean(self.timing_stats['frame_copy']) * 1000
            logger.debug(f"Avg frame copy: {avg_copy:.2f}ms")
            self.timing_stats['frame_copy'].clear()

    def cleanup(self):
        """Clean up resources"""
        logger.info("Save thread cleanup starting...")

        if self.executor is not None:
            self.executor.shutdown(wait=True)
            self.executor = None

        logger.info(f"Save thread stopped. Saved: {self.total_frames_saved} frames, "
                   f"{self.cubes_written} cubes, dropped: {self.total_frames_dropped}")

    def stop(self):
        """Stop save thread"""
        self.running = False
