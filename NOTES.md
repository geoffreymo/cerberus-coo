# Cerberus-COO Known Issues and Notes

## High Frame Rate Limitation (100 Hz Full Frame) - Jan 5, 2026

At 100 Hz full frame (2304x4096 @ 100fps = ~1.88 GB/s), the system can experience frame corruption when disk I/O cannot keep up with the data rate.

### Symptoms
- Framestamps jump forward by exactly the camera buffer size (e.g., 200 or 500 frames)
- Timestamps show data from several seconds in the future mixed into current frames
- Occurs when FITS write threads hold the GIL long enough for DCAM ring buffer to overwrite

### Root Cause
The DCAM ring buffer gets overwritten before frames are read when:
1. Disk writes back up (FITS files are ~1.8 GB each)
2. ThreadPoolExecutor write threads hold the GIL
3. Camera thread stalls longer than buffer_size/fps seconds

### Mitigations Applied
- Increased camera buffer_size from 200 to 500 (5 seconds margin at 100 Hz)
- Reduced writer threads from 4 to 2 (less GIL contention)
- Simplified save thread architecture (removed shared memory complexity)

### Recommended Usage
- **10-30 Hz**: Works reliably with no frame drops
- **100 Hz**: May experience occasional frame corruption under sustained capture
- For 100 Hz, consider using ROI (region of interest) to reduce data rate

### Technical Details
- Camera: Hamamatsu qCMOS (2304x4096, 16-bit)
- Frame size: ~18.9 MB
- At 100 Hz: ~1.88 GB/s sustained write required
- ThreadPoolExecutor used instead of ProcessPoolExecutor (fork() corrupts DCAM buffers)
