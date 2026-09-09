# Cerberus-COO — Hardware & Acquisition Layer Review (fresh eyes)

**Scope:** `hardware/camera/controller.py`, `hardware/dcam/dcam.py` (called parts), `acquisition/save_thread.py`, `hardware/gps_timing.py`, `hardware/telescope/client.py`, `filterwheel/filterwheel.py`.
**Method:** read-only review of the working tree at commit `b3e7262` (2026-06-22). Every claim below was checked against the current source; where hardware behaviour matters I checked the vendor headers installed on this machine (`/usr/local/include/mbglib/pcpsdefs.h`, `/usr/include/libasi/EFW_filter.h`) and the installed `haletcs` package (`/home/hades/cerberus/haletcs/haletcs/client.py`). Two numbers were measured with the project interpreter (`miniconda3/envs/qcmos-control`, astropy 7.1.1, numpy 2.3.4): `Time(unix).isot` = 49 us/call; `fits.writeto` of a uint16 cube allocates ~2x the cube on top of the input.
**Numbering:** new findings are H50+. Previously reported items (#1–#49) are only re-listed in section 3 with a verified status.

---

## 1. Summary table

| ID | Severity | File:line | Summary |
|----|----------|-----------|---------|
| H50 | HIGH | controller.py:315-364 | Ring-buffer overwrite race: overflow checked once before a 200-frame batch, off-by-one (`>` vs `>=`), 10-frame margin after a jump; overwritten/torn frames are still queued to FITS. |
| H51 | HIGH | controller.py:386-395, gps_timing.py:207-264 | GPS<->frame pairing is blind FIFO pop-per-frame with no resync/validation; any one-off surplus or deficit (late pulse, double edge, ring-buffer jump) shifts GPSTIME for the rest of the capture. Matches #48 "pattern 2". |
| H52 | MEDIUM | gps_timing.py:23-30, 259-264 | `PCPS_HR_TIME` ctypes layout does not match Meinberg's packed struct: `signal` field actually holds `utc_offs`, `status` mixes status(u16)+channel(u8). Status flags (INVT, UCAP_OVERRUN, UCAP_BUFFER_FULL, ANT_FAIL) are never checked. |
| H53 | HIGH | save_thread.py:233-234, 297-301; api/cerberus.py:552-556, 629-642 | If the save thread dies at start-up (`os.makedirs` on an unmounted/unwritable `/data`), the camera keeps pushing 19 MB frames into an orphaned 50 000-slot queue; state still says "saving", `frames_saved` = 0, RAM grows until OOM. |
| H54 | HIGH | save_thread.py:255-265, 273-274, 315-387 | If `_write_cube_async` raises (MemoryError on the cube copy is the realistic trigger), `current_frame_idx` is never reset: every later frame is silently discarded and uncounted, and `cube_index` increments ~1000x/s. |
| H55 | HIGH | controller.py:398-402; save_thread.py:214-215, 367-376; api/cerberus.py:552 | No backpressure anywhere: queue "maxsize=50000" = 950 GB of full frames, `pending_writes` unbounded, config `max_queue_size`/`max_pending_writes`/`backpressure_threshold` unused. A slow disk turns into an OOM kill that loses everything queued, and `total_frames_dropped` never increments. |
| H56 | MEDIUM | controller.py:570-604 | `stop_streaming` "force" path (lock not acquired within 2 s) only clears `_capturing`; `cap_stop`/`buf_release` are skipped, the DCAM buffer (up to ~9 GB) stays allocated and the camera keeps capturing; next `start_streaming` cannot allocate. Returns True anyway. |
| H57 | HIGH | client.py:252-287, 347-377 (haletcs client.py:198-261) | Status-channel `TCSClient` has no lock (only the command client got one in fix #1). It is used concurrently by the Tk status poll and the focus-loop thread; haletcs flushes the socket before every send, so responses cross-talk. |
| H58 | MEDIUM | controller.py:307-313, 340-341 | Any DCAM error other than timeout in `wait_capevent_frameready`/`cap_transferinfo`/`buf_getframe...` is silent: no log, `_frame_index` not advanced, and `_main_loop` re-calls immediately -> 100 % CPU spin with no diagnostic (e.g. fibre unplugged mid-run). |
| H59 | MEDIUM | controller.py:388-402, 422 | TOCTOU on `self.save_queue` / `self._gps_device` (set to None by API from another thread). Resulting AttributeError escapes before `_frame_index += 1`, so the same slot is re-read: duplicated frame to callbacks, spurious "framestamp gap", validation disabled. |
| H60 | MEDIUM | filterwheel.py:21-31, 48, 78, 111 | Every EFW return code ignored. A failed `EFWGetPosition` leaves `pos` = 0 -> `wait_for_move` returns immediately and `filter` reports slot 0 ("clear") -> wrong FILTER header / wrong-filter focus runs. `EFWGetProperty` returning `EFW_ERROR_MOVING` right after open (documented) -> `num_slots` = 0 -> every move raises `ValueError`. |
| H61 | MEDIUM | controller.py:433-436, 513, 531, 932-938; dcam.py:561-568; controller.py:708-746 | Lock discipline is inconsistent with the "one thread does all DCAM calls" design: capture-control paths call `prop_*` under the capture lock only, `buf_getframe...` does 1-2 `dcamprop_getvalue` per frame, and `get_property`/`set_property` run DCAM calls on the caller's (GUI) thread under a different lock. `set_property` also triggers a full property enumeration each time. |
| H62 | MEDIUM | save_thread.py:257-258, 64-67; controller.py:551-559 | `DATE-OBS` ("UTC at exposure start") is the wall-clock when the save thread *dequeued* the first frame of the cube; with unbounded queues it lags by the backlog. `time_after_cap_start` is recorded and never used. |
| H63 | MEDIUM | save_thread.py:422-434, 389-420 | Final-cube write failures are silently ignored in `_wait_for_pending_writes` (no log at all for a False result — disk-full at end of night is invisible). 60 s per future serially vs. the API's 60 s `join` -> stats read before completion. |
| H64 | LOW | save_thread.py:233, 324-327, 147 | `session_timestamp` has 1 s resolution and `writeto(overwrite=True)`: stop/start saving within the same second silently overwrites `cube001`. |
| H65 | LOW | controller.py:404-411, 779-781 | `_fps` is never reset on stop; `get_frame_rate()` reports the last value forever. |
| H66 | LOW | controller.py:373, 380 | Rollover thresholds hard-coded (4000 s / 60000) while `config.json` carries `instrument.*_rollover_threshold`; with adaptive buffers up to 10 000 frames a rollover coinciding with a >5536-frame gap is missed (corrected framestamp jumps back by 65536). |
| H67 | LOW | gps_timing.py:238-240; save_thread.py:140 | "ns precision" is overstated: float64 Unix seconds at 1.7e9 resolve ~0.24 us (2^-22 s). |
| H68 | LOW | controller.py:236-270, 158-182 | Retry loop in `_connect_camera` replaces `self.dcam` without `dev_close()` of a possibly-opened handle; `connect()`'s 30 s wait can expire before 3 retries finish (each retry = ~13 full property enumerations + 2 s sleep) leaving a thread running that a later `connect()` calls "already running". |
| H69 | MEDIUM | controller.py:548-549, 388-395; gps_timing.py:151-171 | Shared GPS device is unsafe with two streaming cameras: both pop the same FIFO, and camera B's `start_streaming` calls `clear_buffer()` which discards camera A's pending entries -> permanent GPS offset for A. |
| H70 | LOW | controller.py:853; gps_timing.py:293-296 | `datetime.utcnow()` is deprecated in 3.12; `GPSTimingDevice.__del__` takes a lock during interpreter teardown; `connect()` twice leaks a device handle. |

---

## 2. Findings

### H50 — HIGH — Ring-buffer overwrite race in the batch read
**File:** `hardware/camera/controller.py:315-364`

```python
total_captured = transfer_info.nFrameCount
frames_behind = total_captured - self._frame_index
...
if frames_behind > self.buffer_size:                       # line 323
    ...
    self._frame_index = total_captured - self.buffer_size + 10
    skip_validation = True
max_batch = min(frames_behind, 200)                        # line 332
for i in range(max_batch):
    ...
    result = self.dcam.buf_getframe_with_timestamp_and_framestamp(frame_index_safe)
    ...
    if not skip_validation and self._last_validated_framestamp >= 0:
        ...
        if gap < self.buffer_size:
            logger.warning("Framestamp gap ...")
        else:
            logger.warning("Framestamp mismatch ..., disabling validation")
            skip_validation = True
    self._last_validated_framestamp = framestamp % 65536
    self._process_frame(npBuf, timestamp, framestamp)     # always enqueued
```

**What's wrong**
1. The ring holds frames `[nFrameCount-buffer_size, nFrameCount-1]`. `frames_behind == buffer_size` means the oldest unread slot is the one the *next* incoming frame overwrites; the check should be `>=` and should keep a margin.
2. `nFrameCount` is sampled once, then up to 200 frames are copied (full frame ~19 MB each; the batch takes on the order of a second at 100 Hz, more under GIL contention from the writer threads). Frames keep arriving during the batch; nothing re-checks the margin. Slots near the end of the batch can be recycled before (or while) they are copied — the copied image may even be torn.
3. After an overflow the code jumps to the *oldest* end with only a 10-frame margin (100 ms at 100 Hz) and disables validation, so the recovery batch itself is the most likely place to read recycled slots, undetected.
4. The framestamp validation only *logs*. A recycled slot produces `gap >= buffer_size`, which prints "mismatch, disabling validation" and then still calls `_process_frame` — the frame goes to the FITS cube with its future timestamp. This is exactly the symptom recorded in NOTES.md ("framestamps jump forward by exactly the camera buffer size", "timestamps from several seconds in the future mixed into current frames"). Commit `599c5e6` added "mid-batch overflow detection"; the current code no longer drops anything.

**Failure scenario:** 100 Hz full frame, `buffer_size` 500. A disk stall backs up the writer threads, the camera thread is slow for a second, `frames_behind` = 450. Batch of 200 starts at the oldest end; after ~50 frames (0.5 s) the camera has written 50 new frames and is overwriting the slots the loop is about to copy. The remaining ~150 frames of the batch are copies of frames from ~5 s in the future, flagged only by a warning, and saved.

**Fix (sketch):**
```python
SAFETY = max(20, self.buffer_size // 10)          # or frames arriving during one batch: fps * batch_seconds
...
if frames_behind >= self.buffer_size - SAFETY:
    lost = frames_behind - (self.buffer_size - SAFETY)
    self._frames_lost += lost
    self._frame_index += lost
    frames_behind -= lost
    skip_validation = True
for i in range(max_batch):
    if i % 20 == 0:                                # re-check margin mid-batch
        ti = self.dcam.cap_transferinfo()
        if ti is not False and ti.nFrameCount - self._frame_index >= self.buffer_size - SAFETY:
            break                                  # outer loop re-enters and jumps
    result = self.dcam.buf_getframe_with_timestamp_and_framestamp(self._frame_index % self.buffer_size)
    ...
    if self._last_validated_framestamp >= 0:
        gap = (framestamp - self._last_validated_framestamp - 1) % 65536
        if gap >= self.buffer_size - SAFETY:       # slot was recycled -> this is not frame _frame_index
            logger.error("Recycled slot at %d (gap %d): dropping frame", self._frame_index, gap)
            self._frames_lost += 1
            self._frame_index += 1                 # or resync: self._frame_index += gap + 1 and re-read
            continue                               # do NOT enqueue
```
Also expose `_frames_lost` so the API/GUI can show camera-side losses (today only the save thread has a `dropped` counter, and it is never incremented — see H55).

---

### H51 — HIGH — GPS-to-frame association is blind FIFO pairing with no resync
**File:** `hardware/camera/controller.py:386-395`, `hardware/gps_timing.py:207-264`

```python
if self._gps_per_frame or self._gps_start_timestamp is None:
    gps_ts = self._gps_device.get_timestamp()        # pops exactly one UCAP entry
    if gps_ts is not None:
        gps_unix = gps_ts.unix_seconds
```

**What's wrong:** Each processed frame pops one entry from the Meinberg FIFO. Nothing checks that the popped entry belongs to this frame. Once the FIFO is out of step by one, it stays out of step for the rest of the capture, and nothing detects it:
* **Deficit** (pulse registered after the frame was copied): frame N gets `None`/NaN; frame N+1 pops N's pulse; every later frame is one behind. This is the persistent one-frame offset described as "pattern 2" in #48.
* **Surplus** (double edge #49, spurious pulse at `cap_start`, or the entries of frames lost in a ring-buffer jump — H50 jumps `_frame_index` but never drains the FIFO): every later frame is one or more *ahead*.
* The two Meinberg flags that exist precisely for this — `PCPS_UCAP_OVERRUN` (0x2000, "events interval too short") and `PCPS_UCAP_BUFFER_FULL` (0x4000, "events read too slow") — are stored in `status` (see H52 for the layout) but never inspected.
* `get_buffer_count()` exists but is never used by the controller to notice that the FIFO holds more entries than frames read.

**Failure scenario:** 10 Hz, 1000-frame cube. One READOUTEND pulse is captured a few hundred microseconds after DCAM signals FRAMEREADY (pipeline-stage difference, as #48 documents). GPSTIME for frames N+1..1000 is the time of frame N..999; `TIMESTAMP` (camera) and `GPSTIME` disagree by one frame period; nothing in the log says so.

**Fix (sketch):** validate against the camera clock, drain stale entries, refuse to pop entries that are too new, and honour the status bits:
```python
def _gps_for_frame(self, corrected_timestamp):
    dev = self._gps_device
    if dev is None: return None
    period = self._frame_period            # from INTERNAL_FRAME_INTERVAL at start
    expected = None
    if self._gps_last is not None:         # (gps_unix, camera_ts) of last tagged frame
        expected = self._gps_last[0] + (corrected_timestamp - self._gps_last[1])
    for _ in range(8):                      # bounded drain
        ts = dev.peek_timestamp()           # new: read entries + event without discarding on mismatch is not possible,
        if ts is None: return None          # so pop, but *classify* before accepting:
        if ts.status & (PCPS_UCAP_OVERRUN | PCPS_UCAP_BUFFER_FULL | PCPS_INVT):
            logger.warning("UCAP status 0x%04x on frame %d", ts.status, self._frame_index)
        if expected is None or abs(ts.unix_seconds - expected) < 0.5 * period:
            self._gps_last = (ts.unix_seconds, corrected_timestamp); return ts.unix_seconds
        if ts.unix_seconds < expected:       # stale entry (belongs to a lost/earlier frame): discard, try next
            self._gps_stale += 1; continue
        # entry is newer than this frame: this frame's pulse is missing; keep entry for next frame
        self._gps_pushback = ts; return None
```
Simpler minimum: on every frame compare `dev.get_buffer_count()` with 1 before popping and log/drain surpluses; on a ring-buffer jump call `dev.clear_buffer()`; check the status bits. Also worth recording per-frame `time.time()` in the tuple so the reduction pipeline can detect the offset offline.

---

### H52 — MEDIUM — `PCPS_HR_TIME` ctypes layout does not match the Meinberg struct; status flags never validated
**File:** `hardware/gps_timing.py:23-30`, `259-264`

```python
class PCPS_HR_TIME(ctypes.Structure):
    _fields_ = [("tstamp_sec", c_uint32), ("tstamp_frac", c_uint32),
                ("signal", c_uint32), ("status", c_uint32)]
```
`/usr/local/include/mbglib/pcpsdefs.h:1222-1231` (with `_USE_PACK` -> `#pragma pack(1)`, enabled by default in `use_pack.h:38-39`):
```c
typedef struct {
  PCPS_TIME_STAMP tstamp;      // uint32 sec @0, uint32 frac @4
  int32_t utc_offs;            // @8
  PCPS_TIME_STATUS_X status;   // uint16 @12
  PCPS_SIG_VAL signal;         // uint8  @14  "signal strength ... or capture input channel number"
} PCPS_HR_TIME;                // 15 bytes packed
```
So in the Python struct `signal` = `utc_offs`, and `status` = `status | (channel << 16) | (garbage << 24)`. The seconds/fraction offsets are right, so timestamps themselves are correct today (the 16-byte Python buffer is also large enough), but:
* the field the previous report proposes to filter on for #49 (`signal`) is not the signal/channel byte;
* `PCPS_INVT` (0x80), `PCPS_ANT_FAIL` (0x0200), `PCPS_UCAP_OVERRUN` (0x2000), `PCPS_UCAP_BUFFER_FULL` (0x4000) and the absence of `PCPS_SYNCD` (0x04) are never checked — a free-running/unlocked card still yields "valid" GPSTIME values because the only validation is `unix_time < 946684800`.

**Fix:**
```python
class PCPS_HR_TIME(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("tstamp_sec", c_uint32), ("tstamp_frac", c_uint32),
                ("utc_offs", c_int32), ("status", c_uint16), ("signal", c_uint8)]
PCPS_SYNCD, PCPS_INVT = 0x04, 0x80
PCPS_ANT_FAIL, PCPS_UCAP_OVERRUN, PCPS_UCAP_BUFFER_FULL = 0x0200, 0x2000, 0x4000
...
bad = ucap.status & (PCPS_INVT | PCPS_UCAP_OVERRUN | PCPS_UCAP_BUFFER_FULL)
if bad or not (ucap.status & PCPS_SYNCD):
    logger.warning("UCAP entry status=0x%04x channel=%d", ucap.status, ucap.signal)
```
and propagate `status` into the FITS (e.g. a `GPSSTAT` column) so the pipeline can mask bad entries.

---

### H53 — HIGH — Save thread death leaves the camera feeding an orphaned, effectively unbounded queue
**Files:** `acquisition/save_thread.py:233-234, 297-301`; `api/cerberus.py:552-556, 565-570, 629-642` (call site, for context)

```python
# save_thread.run()
self.session_timestamp = time.strftime('%Y%m%d_%H%M%S')
os.makedirs(self.output_dir, exist_ok=True)       # line 234 — raises if /data is unmounted/read-only
...
except Exception as e:
    logger.error(f"Fatal save thread error: {e}")  # thread ends; camera.save_queue still set
```
`start_saving` sets `camera.save_queue = queue.Queue(maxsize=50000)` and returns True *before* the thread has done anything. If `makedirs` (or anything before the loop) raises, the thread exits, but `CameraController._process_frame` keeps `put_nowait()`-ing every frame into a queue nobody drains. 50 000 x 18.9 MB = 945 GB, so `queue.Full` never triggers; RSS grows at the data rate (1.9 GB/s at 100 Hz, ~19 MB/s at 1 Hz -> a 64 GB host is exhausted within the hour) until the OOM killer takes the process. Meanwhile `cam_state.is_saving` stays True and `update_status` reports `frames_saved = 0`.

**Fix:** create the directory synchronously in `start_saving` (fail fast, return False); in the save thread's fatal path, detach itself (`self.save_queue = None` is not enough — it needs a callback to clear `camera.save_queue`, or the controller should check a `save_thread.failed` flag); and bound the queue by bytes (see H55).

---

### H54 — HIGH — A failed `_write_cube_async` silently discards every subsequent frame
**File:** `acquisition/save_thread.py:255-265, 273-274, 315-387`

```python
if self.current_frame_idx < self.frames_per_cube:      # line 255
    ... store frame ...; self.current_frame_idx += 1
...
if self.current_frame_idx >= self.frames_per_cube:     # line 273
    self._write_cube_async()
```
```python
def _write_cube_async(self):
    try:
        self.cube_index += 1                             # line 318
        frames_copy = self.frame_buffer[:num_frames].copy()   # line 331  (full cube copy)
        ...
        self.current_frame_idx = 0                       # line 379  — only reached on success
    except Exception as e:
        logger.error(f"Write cube error: {e}")           # idx NOT reset
```
If the copy (or `executor.submit`) raises, `current_frame_idx` stays at `frames_per_cube`. From then on every dequeued frame fails the guard on line 255 and is dropped without incrementing any counter, and line 273 re-invokes `_write_cube_async` on every loop iteration (~1000 Hz because of the 1 ms `get` timeout), bumping `cube_index` and printing a traceback each time.

**Realistic trigger:** `MemoryError`. A cube of 1000 full frames (`config.json` `frames_per_cube: 1000`) is 18.9 GB; `_write_cube_async` copies it (another 18.9 GB) and `fits.writeto` of a uint16 array allocates about 2x the cube again (measured: 210 MB peak for a 105 MB cube). With two writers and an unbounded pending list (H55) that is >75 GB of transient allocations per in-flight cube.

**Fix:**
```python
except Exception as e:
    logger.error(...)
    self.total_frames_dropped += self.current_frame_idx
    self.current_frame_idx = 0                          # never leave the buffer "full"
    self.cube_start_time = None
```
and move `self.cube_index += 1` after a successful `submit`.

---

### H55 — HIGH — No backpressure; `total_frames_dropped` can never be non-zero
**Files:** `hardware/camera/controller.py:398-402`; `acquisition/save_thread.py:214-215, 367-376, 389-420`; `api/cerberus.py:552`

* Camera side: `put_nowait` / `except queue.Full: pass` — a full queue drops the frame silently and counts nothing (for a small ROI the 50 000 limit *is* reachable: 50 000 x 0.1 MB = 5 GB).
* Save side: `executor.submit` appends to `pending_writes` without limit; each pending future pins a full cube copy in the executor's work queue. `NUM_WRITERS = 2` limits *concurrency*, not *memory*.
* `total_frames_dropped` is assigned in `__init__` and printed in `cleanup()`; there is no `+=` anywhere (`grep` confirmed). `_check_pending_writes` logs failed cubes (line 411) but does not count them. This is #10, still open, and broader than described there.
* `config.json` carries `acquisition.max_queue_size`, `max_pending_writes`, `backpressure_threshold`, `thread_pool_workers`; none is referenced outside `config.py`/the API's config dump.

**Failure scenario:** disk throughput drops below the data rate for a minute. Nothing throttles; RSS grows by the whole backlog; the OOM killer then loses *all* buffered frames plus the partially written cube, instead of a controlled, counted drop of the excess.

**Fix:** (a) size the camera queue in frames from a byte budget (`max_queue_bytes // frame_bytes`) at `start_saving`; (b) in `_process_frame`, on `queue.Full` increment a controller-side `frames_dropped` counter that the API surfaces; (c) in `_write_cube_async`, if `len(self.pending_writes) >= max_pending_writes`, either block on the oldest future (frames stay in the bounded queue) or drop the cube and add `num_frames` to `total_frames_dropped`; (d) count failed cubes as dropped in `_check_pending_writes`/`_wait_for_pending_writes`.

---

### H56 — MEDIUM — `stop_streaming` force path leaves DCAM capturing and the buffer allocated
**File:** `hardware/camera/controller.py:570-604`

```python
if DCamLock.acquire_capture(self._camera_index, timeout=2.0):
    ... result = self._stop_capture_internal()
else:
    result = self._stop_capture_internal(force=True)   # -> only self._capturing = False
self._stop_requested.clear()
return result                                          # always True
```
`_stop_capture_internal(force=True)` skips `cap_stop()`, `buf_release()` and `_reset_trigger_state()`. The camera thread checks `_stop_requested` only every 50 frames inside the batch (line 334), and the batch holds the capture lock for its whole duration; a 200-frame full-frame batch under GIL contention can exceed 2 s. After the force path: the driver keeps DMA-ing into a buffer of up to ~9 GB that is never released, the trigger source is not restored, `is_streaming()` says False, and the next `start_streaming` calls `buf_alloc` on a still-running capture (line 489) which fails — the only recovery is disconnect/reconnect. `stop_streaming` also reports success.

**Fix:** never stop from the caller's thread when the camera thread owns the lock. Set `_stop_requested`, then wait on an `Event` that `_capture_frame` sets in its `finally` (or wait for the lock without the 2 s cap), and only then `cap_stop`/`buf_release`. Alternatively have the camera thread perform the stop itself when it sees `_stop_requested` (it already owns the handle), and make `stop_streaming` return the real result.

---

### H57 — HIGH — TCS status channel is unlocked and used from two threads
**File:** `hardware/telescope/client.py:252-287, 347-377`; `haletcs/client.py:198-261` (installed package)

Fix #1 added `_cmd_lock` around `_cmd_client` only. `get_focus`, `get_focus_status`, `get_position`, `get_status` and `wait_for_focus` use `_status_client` with no lock. haletcs `TCSClient` has no internal lock, and `_send_command` starts by *flushing whatever is in the receive buffer*, then sends and reads until `\0`/`\n`.

Verified concurrent callers: `gui/app.py:417-430` runs `api.update_status()` on the Tk thread every second, which does `telescope.get_focus()`, `get_position()`, `get_status()` (api 1482-1484); the focus loop runs in a background thread (`gui/focus_window.py:412,442`) and calls `telescope.wait_for_focus()` -> `get_focus()` every 0.5 s (client.py:316) plus `telescope.get_status()` (focus_sequence.py:358).

**Failure scenario:** thread A sends `REQPOS`; before A's `recv`, thread B enters `_send_command`, its non-blocking flush eats A's reply (or B's `recv` returns A's reply and parses it as a status string). A gets a timeout/`TCSError` -> `get_focus` returns None -> `wait_for_focus` logs "retrying"; or A gets wrong-but-numeric values and `wait_for_focus` returns early, so the focus sequence exposes at the wrong focus. Over a 60-step focus loop with ~5 status round-trips per second, collisions are likely.

**Fix:**
```python
self._status_lock = threading.Lock()
def get_status(self):
    if not self._is_connected or self._status_client is None: return None
    with self._status_lock:
        try: return self._status_client.get_status()
        except Exception as e: ...
```
(same for `get_position`, `get_focus`, `get_focus_status`; `wait_for_focus` should take the lock per poll via `get_focus`, not for the whole loop).

---

### H58 — MEDIUM — DCAM errors in the capture loop are silent and cause a busy spin
**File:** `hardware/camera/controller.py:307-313, 340-341`; `_main_loop` 276-284

```python
if not self.dcam.wait_capevent_frameready(timeout_ms):   # False for TIMEOUT *and* every other error
    return
transfer_info = self.dcam.cap_transferinfo()
if transfer_info is False:
    return
...
result = self.dcam.buf_getframe_with_timestamp_and_framestamp(frame_index_safe)
if result is False:
    break                                                # no log, _frame_index unchanged
```
`Dcam.wait_event` returns False for any failure; `DCAMERR` has `is_timeout()` (dcamapi4.py:154) but it is never consulted. On ABORT / LOSTFRAME / INVALIDHANDLE / device removed, `_capture_frame` returns immediately and `_main_loop` calls it again with no sleep: a silent 100 % CPU loop, `is_streaming()` still True, no log line. A persistent `copyframe` failure re-reads the same slot forever while the camera advances -> overflow.

**Fix:**
```python
if not self.dcam.wait_capevent_frameready(timeout_ms):
    err = self.dcam.lasterr()
    if not err.is_timeout():
        logger.error("wait_capevent failed: %s", err)
        self._capture_error = err; self._capturing = False   # or back off and notify via callback
    return
```
and log `lasterr()` for the `transferinfo`/`copyframe` failures, advancing `_frame_index` (and counting a drop) when a slot cannot be copied.

---

### H59 — MEDIUM — TOCTOU on `save_queue` / `_gps_device` duplicates a frame and poisons validation
**File:** `hardware/camera/controller.py:388-402, 422`

```python
if self._gps_device is not None:
    ... gps_ts = self._gps_device.get_timestamp()       # API disconnect_gps() sets it to None concurrently
if self.save_queue is not None:
    self.save_queue.put_nowait(...)                     # API stop_saving() sets it to None concurrently
...
self._frame_index += 1                                  # line 422 — not reached if the above raised
```
`disconnect_gps` (api 947-948) and `stop_saving` (api 682) set these attributes from another thread. An `AttributeError` propagates out of `_process_frame` and the batch loop to `_main_loop`'s generic handler. Because `_last_validated_framestamp`, `_last_raw_timestamp` were already updated but `_frame_index` was not, the next batch re-reads the same slot: the frame is delivered to callbacks (and possibly the queue) twice, the validator sees `actual == expected - 1` -> gap 65535 -> "mismatch, disabling validation".

**Fix:** snapshot to locals at the top of `_process_frame` (`q = self.save_queue; gps = self._gps_device`) and increment `_frame_index` in a `try/finally` (or before processing).

---

### H60 — MEDIUM — FilterWheel ignores every EFW SDK return code
**File:** `filterwheel/filterwheel.py:21-31, 40-49, 74-79, 107-114`

```python
self.lib.EFWGetID(0, ctypes.byref(self.wheel_id))     # rc ignored
self.lib.EFWOpen(self.wheel_id.value)                  # rc ignored
self.num_slots = self._get_slot_count()                # EFWGetProperty rc ignored -> slotNum 0 on error
...
pos = ctypes.c_int()
self.lib.EFWGetPosition(self.wheel_id.value, ctypes.byref(pos))   # rc ignored
return pos.value                                       # 0 on error
```
From `/usr/include/libasi/EFW_filter.h`: `EFWGetPosition` returns `EFW_ERROR_CLOSED`, `EFW_ERROR_REMOVED`, `EFW_ERROR_ERROR_STATE` (lines 144-148) and sets `*pPosition = -1` only while moving (line 141); `EFWGetProperty` returns `EFW_ERROR_MOVING` "generally soon after filter wheel is connected" (line 126). Consequences:
* wheel unplugged/errored -> `position` == 0 -> `wait_for_move` returns True at once, `goto` returns "done", and `filter` reports the slot-0 name ("clear" in `config.json`) -> FITS `FILTER` header and the focus-loop's per-filter results are silently wrong;
* `EFWGetProperty` failing at construction (only a fixed 0.5 s sleep guards it) -> `num_slots = 0` -> every `position = n` raises `ValueError("Position must be 0--1")`.

**Fix:**
```python
def _check(self, rc, what):
    if rc != 0: raise RuntimeError(f"{what} failed: EFW error {rc}")
...
self._check(self.lib.EFWGetID(0, byref(self.wheel_id)), "EFWGetID")
self._check(self.lib.EFWOpen(self.wheel_id.value), "EFWOpen")
for _ in range(20):                                    # slot detection may still be running
    rc = self.lib.EFWGetProperty(self.wheel_id.value, byref(info))
    if rc == 0: break
    if rc != EFW_ERROR_MOVING: self._check(rc, "EFWGetProperty")
    time.sleep(0.25)
@property
def position(self):
    pos = ctypes.c_int(-2)
    self._check(self.lib.EFWGetPosition(self.wheel_id.value, byref(pos)), "EFWGetPosition")
    return pos.value
```
Also note the class has no lock; the API wraps *moves* in `_filterwheel_lock` but the save thread's `filter_callback` and `update_status` read `.filter` unlocked while a move may be in progress.

---

### H61 — MEDIUM — DCAM lock discipline does not match the stated single-thread design
**Files:** `hardware/camera/controller.py:433-436, 513, 531, 923-944` (property calls under the *capture* lock only); `hardware/dcam/dcam.py:561-568` (`dcamprop_getvalue(FRAMEBUNDLE_MODE)` on every `buf_getframe_with_timestamp_and_framestamp`); `controller.py:708-746` (`get_property`/`set_property` execute DCAM calls on the caller's thread under the *property* lock); `controller.py:876-898` (`_apply_defaults` -> 12x `set_property` -> 12x `_update_camera_params` full enumeration).

The module docstring says "ONE thread handles all DCAM calls". In practice: `start_streaming`/`stop_streaming`/`capture_single`/`set_property`/`get_property` run DCAM calls on whatever thread calls them (the Tk thread today), and the two RLocks are not nested, so a `get_property('SENSOR_TEMPERATURE')` from the status poll runs `dcamprop_getvalue` concurrently with the camera thread's per-frame `dcamprop_getvalue(FRAMEBUNDLE_MODE)` + `dcambuf_copyframe`, and `start_streaming`'s `prop_getvalue/prop_setgetvalue` run concurrently with a status poll. Whether DCAM serialises internally is a vendor question; #46 (multi-second blocking in `prop_getvalue` during long exposures) suggests it does, which is exactly why these calls freeze the GUI. `_update_camera_params` after *every* `set_property` is also expensive (hundreds of round-trips) and is what makes exposure changes slow.

**Fix:** route all DCAM calls through the camera thread (a small command queue processed in `_main_loop`'s idle branch; callers wait on a `Future`) — this removes both the cross-thread DCAM usage and the GUI freezes in one move. Short-term: in the capture-control paths, take the property lock as well before touching `prop_*`; drop the per-frame `FRAMEBUNDLE_MODE` query by caching it at `buf_alloc` time (also relevant to #47).

---

### H62 — MEDIUM — `DATE-OBS` is the dequeue time, not the exposure start
**Files:** `acquisition/save_thread.py:257-258, 64-67`; `hardware/camera/controller.py:551-559`

```python
if self.current_frame_idx == 0:
    self.cube_start_time = time.time()            # when the save thread *dequeued* the frame
...
primary_hdr['DATE-OBS'] = (exp_start_utc.isoformat(), 'UTC at exposure start')
```
With the unbounded queue (H55) the save thread can run tens of seconds behind the camera, so `DATE-OBS` drifts by the backlog while claiming to be the exposure start. The controller records `time_before_cap_start`/`time_after_cap_start` (lines 551, 559) but nothing consumes them.

**Fix:** put a wall-clock (or better `time_after_cap_start + corrected_timestamp - first_timestamp`) into the queued tuple from the camera thread, and derive `DATE-OBS` from the first frame's camera timestamp; keep the dequeue time as a separate `DATE-WRT` if useful.

---

### H63 — MEDIUM — Final-cube failures are invisible; shutdown accounting races the API
**File:** `acquisition/save_thread.py:422-434` (and `389-420`)

```python
result = future.result(timeout=60)
if isinstance(result, tuple) and result[0]:
    ... self.total_frames_saved += result[3]
# no else: a (False, path, 0, 0, "OSError: No space left on device") is dropped on the floor
```
The last cube of a run (the one most likely to hit a full disk) can fail with no log line at all; `_check_pending_writes` at least logs. `future.result(timeout=60)` is per future and serial, whereas `stop_saving` joins the thread with a single 60 s timeout (api 688) and then reads `total_frames_saved` — with several cubes pending the API reports incomplete numbers, and the daemon thread keeps writing after "stop" returned.

**Fix:** log and count the failure branch (`else: logger.error(...); self.total_frames_dropped += ...`), wait with one overall deadline, and let the API poll `save_thread.is_alive()` / a `finished` Event rather than a fixed join.

---

### H64 — LOW — Filename collision within one second silently overwrites data
**File:** `acquisition/save_thread.py:233, 324-327`; `147` (`overwrite=True`)
Restarting a save within the same wall-clock second (double-click, script) reuses `YYYYMMDD_HHMMSS_<cam>_<obj>_cube001.fits` and `writeto(..., overwrite=True)` replaces the earlier file. Use `overwrite=False` plus a uniqueness check, or add sub-second/serial component.

### H65 — LOW — Stale FPS after stop
**File:** `hardware/camera/controller.py:404-411, 779-781`. `_fps` is only updated when a frame arrives and never cleared in `_stop_capture_internal`; reset it there and expose "no frames in the last N s" as 0.

### H66 — LOW — Hard-coded rollover thresholds; large-buffer edge case
**File:** `hardware/camera/controller.py:373, 380`. `config.json` `instrument.timestamp_rollover_threshold`/`framestamp_rollover_threshold` are read into `config.py` but the controller hard-codes 4000/60000. With adaptive buffers up to 10 000 frames a rollover that coincides with a gap > 5536 frames is not detected (`framestamp < last - 60000` is false) and `corrected_framestamp` jumps back by 65536. Use `gap = (framestamp - last) % 65536` and treat `gap > 32768` as backward.

### H67 — LOW — "ns precision" is overstated
**File:** `hardware/gps_timing.py:238-240`; `acquisition/save_thread.py:140` (`'Unix seconds (ns precision)'`). float64 at 1.7e9 s resolves 2^-22 s ~ 0.24 us. Either store `sec` and `frac` separately (two columns) or fix the comment.

### H68 — LOW — Connect retry leaks a handle; `connect()` timeout vs retries
**File:** `hardware/camera/controller.py:236-270, 158-182`. On an exception after `dev_open()` succeeded, the next retry replaces `self.dcam` without `dev_close()`. `connect()` waits 30 s, but three retries each run `_apply_defaults` (12 `set_property` -> 12 full enumerations), warmup and a 2 s sleep, so the caller can time out while the thread continues; a later `connect()` then logs "already running". Close the handle before retrying; make the wait cover the retry budget or make retries configurable.

### H69 — MEDIUM — Shared GPS device is unsafe with more than one streaming camera
**Files:** `hardware/camera/controller.py:548-549, 388-395`; `hardware/gps_timing.py:151-171`. The API installs the same `GPSTimingDevice` on every controller. If two cameras stream, both pop the single FIFO (each steals the other's pulses), and camera B's `start_streaming` calls `clear_buffer()` which discards entries camera A has not read yet -> A's GPSTIME column is offset for the rest of its capture (see H51). If only one camera is wired to the Meinberg input, only that controller should get the device; otherwise entries must be routed by the capture-channel byte (`signal` after H52) with one reader thread.

### H70 — LOW — Minor
* `controller.py:853` `datetime.utcnow()` is deprecated in Python 3.12 (use `datetime.now(timezone.utc)`).
* `gps_timing.py:293-296` `__del__` calls `disconnect()` which takes `_lock`; during interpreter shutdown this can raise/hang. `connect()` called twice opens a second device handle without closing the first.
* `client.py:180-196` if `_cmd_client.disconnect()` raises, `_status_client.disconnect()` is skipped (socket leak); close both in separate try blocks.

---

## 3. Previously reported items in scope: status

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 1 | TCS two-socket race | PARTIALLY FIXED | `_cmd_lock` covers the command client (client.py:214, 240, 396, 412, 424, 436, 448). The status client is still unlocked and is used from two threads — see **H57**. |
| 2 | Double release of capture RLock | APPEARS-FIXED | `_capture_frame` (controller.py:299-367) has a single `finally: DCamLock.release_capture(...)`, no early release. |
| 5 | Buffer not released on exception between `buf_alloc` and `cap_start` | VERIFIED-STILL-PRESENT (low likelihood) | controller.py:489-557: no `except` releases the buffer; the window now only contains guarded `prop_*` calls, `time.sleep`, and `self._gps_device.clear_buffer()` (unguarded). Wrap in `try/except: buf_release(); raise`. |
| 6 | `__enter__` ignores `connect()` result | VERIFIED-STILL-PRESENT | client.py:458-461. |
| 10 | Failed writes not counted in `total_frames_dropped` | VERIFIED-STILL-PRESENT | save_thread.py:409-411, 427-430; the counter has no `+=` anywhere (see **H55**). |
| 15 | `status.focus_mm` without None check | N-A | haletcs `get_status()` never returns None — it raises `TCSError`/`TCSConnectionError`, which client.py:265 already catches. |
| 16 | Focus range not validated | VERIFIED-STILL-PRESENT | `_focus_min/_focus_max` are loaded (client.py:89-90, 95-96) but never used in `set_focus`/`offset_focus` (200-250). |
| 18 | 'N/A' strings in numeric headers | VERIFIED-STILL-PRESENT | save_thread.py:90-109. Note the API callback builds the dicts with all keys, so the `'N/A'` default is rarely hit; values may be `None` instead (astropy writes an undefined card). |
| 19 | Bare `except:` in save_thread | VERIFIED-STILL-PRESENT | save_thread.py:85-86, 128-129, 351-352. |
| 26 | Bare `except:` in `_cleanup_clients` | VERIFIED-STILL-PRESENT | client.py:169-170, 174-175. |
| 27 | `tube_length_mm` None check | VERIFIED-STILL-PRESENT (benign) | client.py:281-284; any AttributeError is caught by the broad `except Exception` and returns None with an error log. |
| 28 | Cardinal moves don't log when disconnected | VERIFIED-STILL-PRESENT | client.py:410-411, 422-423, 434-435, 446-447. |
| 29 | haletcs import fallback `TCSError = Exception` | VERIFIED-STILL-PRESENT | client.py:20-25 (haletcs is installed in the project env, so the fallback is inert in practice). |
| 30 | Queue maxsize handling | APPEARS-FIXED | The maxsize check no longer exists; `_process_frame` just does `put_nowait` / `except queue.Full: pass` (controller.py:398-402) — but see **H55** for the silent drop. |
| 31 | Warmup ignores `_running` | VERIFIED-STILL-PRESENT (trivial) | controller.py:636-674 has no `_running` check; warmup lasts ~10 ms. |
| 32 | Plain-bool `running` flag | VERIFIED-STILL-PRESENT (benign on CPython) | save_thread.py:191, 236, 456. |
| 34 | 1 ms queue timeout | VERIFIED-STILL-PRESENT | save_thread.py:241 — ~1000 wakeups/s plus `_check_pending_writes` each iteration; this is GIL pressure on the camera thread. 50-100 ms is safe (the 200-frame batch already buffers). |
| 36 | `frames_per_cube` default mismatch | N-A (config.py, other scope) | Note: `acquisition.frames_per_cube` (1000 in config.json) is not used by the save path at all — the GUI passes its own entry (default "100"). |
| 42 | `current_focus` None in timeout log | APPEARS-FIXED | client.py:341-342 formats `{current_focus}` without a numeric spec, so None cannot raise. |
| 47 | Mangled stamps at ~2 kHz | N-A (not reproducible here) | Code-level candidates: **H50** (batch overwrite race, worst at high rates), per-frame `dcamprop_getvalue(FRAMEBUNDLE_MODE)` in dcam.py:561-568 (see H61), and `np.zeros` allocation per frame (dcam.py:65-86, 570). |
| 48 | Hardware timing stalls | N-A (hardware) | Software side: **H51** explains why "pattern 2" persists once it occurs and how to make it self-heal. |
| 49 | UCAP edge filtering | VERIFIED-STILL-PRESENT, but see **H52** | `signal` is stored, never inspected (gps_timing.py:259-264) — and with the current struct it holds `utc_offs`, not the signal/channel byte. Nothing in the installed Meinberg headers documents an edge indicator in the event itself; a stronger guard is the `PCPS_UCAP_OVERRUN` flag plus time-based validation (**H51**). |

Items #3, #4, #7-9, #11-14, #17, #20-25, #33, #35, #37-41, #43-46 are outside this layer (API/GUI/focusloop/config) and were not re-verified here, except that `focus_sequence.py:376` now calls only `filterwheel.goto()` (consistent with #4 marked fixed).

---

## 4. Notes for a GUI port

**Blocking calls that must not run on the GUI thread (worst-case durations from the code):**
* `CameraController.connect()` — up to 30 s (`_connect_event.wait(30)`), and the retry loop can exceed that (H68).
* `CameraController.disconnect()` — `stop_streaming()` (below) + `join(5 s)`.
* `CameraController.start_streaming()` — up to 3 s lock wait + `_calculate_buffer_size` (3 property reads) + `buf_alloc` of up to ~9.4 GB (driver pins memory; seconds) + up to 1.1 s `align_to_second` sleep + `cap_start`; runs DCAM calls on the caller's thread (H61).
* `CameraController.stop_streaming()` — unconditional `time.sleep(0.2)` + up to 2 s lock wait (+ the force-path hazard, H56).
* `CameraController.capture_single()` — up to `timeout_ms` (30 s default) inside the capture lock.
* `set_property()`/`set_exposure()` — each triggers `_update_camera_params()` = enumeration of *every* DCAM property (hundreds of round-trips); `get_property()`/`get_exposure()` can block for seconds during long exposures (#46). Prefer `get_all_params()` (cached, lock-only) for display.
* `TelescopeController.*` — synchronous socket I/O with a 30 s timeout; `set_focus`/`move_*` block until the TCS finishes the move (haletcs note at client.py:662); `wait_for_focus` up to 60 s.
* `FilterWheel.__init__` (0.5 s sleep + USB), `goto()`/`wait_for_move()` up to 30 s, and even the `.filter`/`.position` getters are USB round-trips.
* `OptimizedSaveThread.stop()` is instant, but `join()` (API `stop_saving`) waits up to 60 s and `cleanup()` waits for all writers.
* `GPSTimingDevice.connect()` (library load + device open) — quick but does ctypes I/O; `get_timestamp()` is per-frame on the camera thread (49 us astropy conversion is fine).

**Thread affinity / where callbacks fire:**
* Frame callbacks registered via `on_frame()` run **on `CameraThread`**, inside `_process_frame`, while `_callback_lock` is held. Anything slow there delays the ring-buffer read (H50). Never touch widgets; hand off through a bounded latest-only queue or a queued Qt signal carrying a reference (do not copy 19 MB per frame on that thread).
* The **same ndarray object** is passed to callbacks and put on the save queue (controller.py:400, 418). A GUI that modifies the frame in place (scaling, background subtraction) corrupts what the save thread later copies into the cube (`save_thread.py:260`). Treat frames as read-only or copy.
* `remove_frame_callback()`/`on_frame()` take `_callback_lock`, so they block the GUI thread while a callback is executing on the camera thread — do not call them from inside a callback (deadlock is avoided only because it is a plain `Lock` on different threads; from the callback itself it would deadlock).
* The API's `_on_camera_frame` (camera thread) takes `_state_lock` every frame; `CerberusAPI.state` deep-copies all camera states including the `params` dict under the same lock. A GUI that polls `api.state` at high rate contends directly with frame processing.
* `OptimizedSaveThread` invokes `filter_callback`, `telescope_callback`, `gps_start_callback` **on `SaveThread`** once per cube; `filter_callback` performs a filter-wheel USB read there (H60 note on locking).
* DCAM calls currently happen on *whichever* thread calls the controller (H61). A Qt port should own one worker (`QThread`) that is the only caller of `connect/start/stop/set_property`, or the controller should be refactored to marshal those onto `CameraThread`.
* `_camera_thread`, `SaveThread` and the writers are daemon/pool threads; on application exit the executor's workers are joined by `concurrent.futures` at interpreter shutdown, but a save thread still accumulating a partial cube is killed — call `stop_saving()` and wait before quitting.

**Status/statistics semantics to be aware of:**
* `get_frame_rate()` is stale after stop (H65); `is_streaming()` is a plain flag that stays True during the silent error spin (H58).
* `frames_saved` only advances when a cube's future completes (per cube, not per frame) and lags by up to one cube; `frames_dropped` is always 0 today (H55).
* `is_saving()` in the API checks `save_thread.is_alive()`, while the per-camera state uses `cam_state.is_saving` — these disagree after H53.
* `start_streaming(align_to_second=True)` sleeps up to ~1.1 s before returning; the port should show a "starting" state rather than freezing.
