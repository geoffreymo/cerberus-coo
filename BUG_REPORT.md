# Cerberus-COO Bug Report
**Generated:** 2026-01-06

## Summary by Severity

| Severity | Count | Description |
|----------|-------|-------------|
| **CRITICAL** | 2 | Race conditions in hardware layer |
| **HIGH** | 8 | Thread safety, statistics errors, connection issues |
| **MEDIUM** | 18 | Thread safety, None checks, error handling |
| **LOW** | 21 | Minor issues, logging, documentation |

---

## CRITICAL Bugs (Fix Immediately)

### 1. ~~Race Condition - Two-Socket TCS Has No Thread Synchronization~~ FIXED
**File:** `hardware/telescope/client.py:127-148`

The two-socket TCS implementation has no locks. If multiple threads call `get_position()` while another calls `set_focus()`, race conditions can occur.

**Fix applied:**
- Added `_cmd_lock` to protect command client methods
- Save thread now reads from cached state instead of querying TCS
- Status polling cached in `self._state`, updated every 500ms

---

### 2. ~~Double-Release of RLock in Camera Capture~~ FIXED
**File:** `hardware/camera/controller.py:277-285`

The capture lock is released inside the `try` block AND in the `finally` block, causing double-release.

**Fix applied:** Added `lock_released` flag to track early release:
```python
lock_released = False
try:
    if result is not False:
        DCamLock.release_capture()
        lock_released = True
        self._process_frame(...)
        return
finally:
    if not lock_released:
        DCamLock.release_capture()
```

---

## HIGH Bugs (Fix Soon)

### 3. ~~Wrong Writer Object for Statistics~~ FIXED
**File:** `api/cerberus.py:1233-1236`

Status updates read from `self.writer` (unused old object) instead of `self.save_thread`. Statistics show 0 frames saved during acquisition.

**Fix applied:** Changed to use `self.save_thread` for statistics.

---

### 4. Double Wait for Filter Wheel
**File:** `focusloop/focus_sequence.py:373-374`

`filterwheel.goto()` already waits, then `wait_for_move()` is called again.

**Fix:** Remove the redundant `wait_for_move()` call:
```python
self.filterwheel.goto(filter_name)  # Already waits internally
# Remove: self.filterwheel.wait_for_move()
```

---

### 5. Buffer Not Released on Exception
**File:** `hardware/camera/controller.py:397-403`

If exception occurs between `buf_alloc()` and `cap_start()`, buffer is leaked.

**Fix:**
```python
buffer_allocated = False
try:
    if not self.dcam.buf_alloc(self.buffer_size):
        return False
    buffer_allocated = True
    ...
except Exception:
    if buffer_allocated:
        self.dcam.buf_release()
    raise
```

---

### 6. Context Manager Ignores Connection Failure
**File:** `hardware/telescope/client.py:447-455`

`__enter__` ignores return value of `connect()`. Failed connection still returns `self`.

**Fix:**
```python
def __enter__(self):
    if not self.connect():
        raise ConnectionError("Failed to connect to TCS")
    return self
```

---

### 7. ~~Double-Counting of Saved Frames~~ N/A - writer.py deleted
**File:** `acquisition/writer.py:344,439`

~~Frames counted as "saved" immediately when queued, but counted again as "dropped" if write fails.~~

**Status:** File deleted - `save_thread.py` is the active implementation and doesn't have this issue.

---

### 8. Tkinter Updates from Background Thread (focus_panel.py)
**File:** `gui/panels/focus_panel.py:215-226`

Direct `progress_var.set()` calls from background threads can cause crashes.

**Fix:**
```python
if result.success:
    msg = f"Done: {result.best_focus:.2f}mm, FWHM={result.best_fwhm_arcsec:.2f}\""
    self.after(0, lambda m=msg: self.progress_var.set(m))
else:
    msg = f"Failed: {result.error_message}"
    self.after(0, lambda m=msg: self.progress_var.set(m))
```

---

### 9. Tkinter Updates from Background Thread (focus_window.py)
**File:** `gui/focus_window.py:495-511`

Same issue as #8 - direct Tkinter variable updates from background thread.

---

### 10. Failed Writes Not Counted in total_frames_dropped
**File:** `acquisition/save_thread.py:336-354`

When writes fail, `total_frames_dropped` is not incremented.

**Fix:** Add `self.total_frames_dropped += nframes` when write fails.

---

## MEDIUM Bugs (Should Fix)

### 11. Missing None Check in set_focus() Logging
**File:** `api/cerberus.py:646-648`

`previous_focus` could be None, causing f-string formatting error.

**Fix:**
```python
previous_str = f"{previous_focus:.2f}" if previous_focus is not None else "None"
logger.info(f"FOCUS CHANGE: {previous_str} -> {position_mm:.2f} mm")
```

---

### 12. Missing None Check in offset_focus() Logging
**File:** `api/cerberus.py:669-671`

Same issue as #11.

---

### 13. Thread-Unsafe State Reads Outside Lock
**File:** `api/cerberus.py:133, 247, 305, 309, 449, 790, 1026-1034, 1111, 1115, 1296, 1332, 1407-1420`

Multiple places access `self._state` attributes without holding `self._state_lock`.

**Fix:** Use lock consistently or take state snapshots under lock.

---

### 14. Silent Failures with Bare except: Clauses
**File:** `api/cerberus.py:345, 384-385, 404-405, 478-479, 746-748`

Bare `except:` clauses swallow all exceptions silently.

**Fix:** Use `except Exception as e:` and log the error.

---

### 15. Missing None Check on status.focus_mm
**File:** `hardware/telescope/client.py:256-258`

Accessing `status.focus_mm` without checking if status is None.

**Fix:**
```python
status = self._status_client.get_status()
if status is None:
    return None
return getattr(status, 'focus_mm', None)
```

---

### 16. Focus Range Validation Missing
**File:** `hardware/telescope/client.py:196-219`

`set_focus()` documents range 1.0-74.0mm but never validates input.

**Fix:**
```python
if position_mm < self._focus_min or position_mm > self._focus_max:
    logger.error(f"Focus position {position_mm} out of range [{self._focus_min}, {self._focus_max}]")
    return False
```

---

### 17. ~~Thread-Unsafe Access to _object_name and _comment~~ N/A - writer.py deleted
**File:** `acquisition/writer.py:464, 482`

**Status:** File deleted - `save_thread.py` passes all values as parameters.

---

### 18. String 'N/A' for Numeric FITS Headers
**File:** `acquisition/save_thread.py:78-97`

Using 'N/A' string for fields like AIRMASS that should be numeric.

**Fix:** Use None or skip adding header if value missing.

---

### 19. Bare except: Clauses in save_thread.py
**File:** `acquisition/save_thread.py:73-74, 116-117, 293-294`

**Fix:** Use `except Exception:` at minimum.

---

### 20. No User Notification on Filter Change Failure
**File:** `gui/panels/camera_controls.py:375-386`

Filter combobox stays on selected value even if hardware change failed.

**Fix:** Restore previous filter value on failure.

---

### 21-22. Missing Focus Thread Cleanup on Window Close
**Files:** `gui/focus_window.py`, `gui/panels/focus_panel.py`

Focus threads not joined when window/panel is closed.

**Fix:** Override `destroy()` to abort and join focus thread.

---

## LOW Bugs (Fix When Convenient)

### 23. Return Type Mismatch in run_focus_loop()
**File:** `api/cerberus.py:1007-1008`

Docstring says `Optional[Dict]` but returns `Dict[Optional[str], FocusResult]`.

---

### 24. Variable Shadowing with Config Module
**File:** `api/cerberus.py:1066`

Local variable `config` shadows parameter and module function.

---

### 25. Directory Creation Race Condition
**File:** `api/cerberus.py:324`

`os.makedirs('')` fails if filepath has no parent directory.

---

### 26. Bare except: in Telescope Cleanup
**File:** `hardware/telescope/client.py:165-166, 170-171`

---

### 27. Missing None Check on tube_length_mm
**File:** `hardware/telescope/client.py:273-278`

---

### 28. Inconsistent Error Logging in Cardinal Move Methods
**File:** `hardware/telescope/client.py:401-443`

`move_north/south/east/west` don't log errors when not connected.

---

### 29. haletcs Import Fallback Creates Type Inconsistency
**File:** `hardware/telescope/client.py:19-24`

Fallback `TCSError = Exception` catches all exceptions.

---

### 30. Queue maxsize Handling Incorrect
**File:** `hardware/camera/controller.py:316-317`

maxsize of 0 means infinite, not that attribute is missing.

---

### 31. Warmup Capture Doesn't Check _running Flag
**File:** `hardware/camera/controller.py:458-496`

---

### 32. Thread-Unsafe running Flag
**File:** `acquisition/save_thread.py:168, 207, 393`

Use `threading.Event()` instead of plain boolean.

---

### 33. ~~Memory Leak in _cube_telescope_data on Error~~ N/A - writer.py deleted
**File:** `acquisition/writer.py:329-333, 417-418`

**Status:** File deleted.

---

### 34. Queue Get Timeout Too Short (1ms)
**File:** `acquisition/save_thread.py:212`

Causes 1000 wakeups/sec. Use 0.1 second timeout.

---

### 35. ~~_timing_info Modified Without Lock~~ N/A - writer.py deleted
**File:** `acquisition/writer.py:320-322, 573-608`

**Status:** File deleted.

---

### 36. frames_per_cube Default Mismatch
**File:** `config.py:77` vs `config.json:78`

Dataclass default is 100, JSON has 1000.

---

### 37. ~~Unused FITSWriter Methods~~ FIXED - FITSWriter deleted
**File:** `acquisition/writer.py`, `api/cerberus.py`

**Status:** Entire `FITSWriter` class and `writer.py` file deleted. Was unused dead code.

---

### 38. FocusPanel Not Used in Main Application
**File:** `gui/app.py`

`FocusPanel` class exists but `FocusWindow` is used instead.

---

### 39. Hardcoded Plate Scale in Focus Window
**File:** `gui/focus_window.py:64-66`

---

### 40. Missing Validation Before Division in Photometry
**File:** `gui/panels/image_display.py:1248-1249`

Very small `comp_flux` values could produce inf.

---

### 41. Stale Filter List Check
**File:** `gui/focus_window.py:679-685`

Minor inefficiency in list comparison.

---

### 42. current_focus May Be None in Timeout Log
**File:** `hardware/telescope/client.py:334-337`

---

### 43. Bare except: in MockCamera
**Files:** `gui/focus_window.py:146-148`, `focusloop/test_focus_loop.py:252-256`

---

## Priority Order for Fixes

### Immediate (Safety Critical)
1. #1 - TCS thread synchronization
2. #2 - Camera lock double-release

### Today (Functional Issues)
3. #3 - Writer statistics
4. #4 - Double filter wheel wait
5. #8, #9 - Tkinter thread safety

### This Week (Robustness)
6. #5 - Buffer release
7. #6 - Context manager
8. #7, #10 - Frame counting
9. #11, #12 - None checks in logging
10. #16 - Focus range validation

### When Convenient
- All LOW severity bugs
- Remaining MEDIUM bugs
