# SPDX-License-Identifier: GPL-3.0-or-later
"""Run network calls off Blender's main thread so the UI never freezes.

Worker threads never touch bpy data. They only put results on a queue, and a
bpy.app.timers callback (on the main thread) hands each result to its
callback."""

import queue
import threading

import bpy

_results = queue.Queue()
_pending = 0
_lock = threading.Lock()
POLL_SECONDS = 0.2


def run(fn, on_done):
    """Call fn() on a worker thread, then on_done(result, error) on the main thread."""
    global _pending
    with _lock:
        _pending += 1

    def work():
        try:
            res, err = fn(), None
        except Exception as e:  # delivered to on_done, never raised on the thread
            res, err = None, e
        _results.put((on_done, res, err))

    threading.Thread(target=work, name="tla-request", daemon=True).start()
    if not bpy.app.timers.is_registered(poll):
        bpy.app.timers.register(poll, first_interval=POLL_SECONDS)


def poll():
    """Timer callback: deliver finished results. Returns None to stop."""
    global _pending
    while True:
        try:
            on_done, res, err = _results.get_nowait()
        except queue.Empty:
            break
        with _lock:
            _pending -= 1
        try:
            on_done(res, err)
        except Exception as e:  # never let a callback kill the timer
            print("Thornbury Lighting Assistant: callback failed:", e)
    with _lock:
        return POLL_SECONDS if _pending > 0 else None


def busy():
    with _lock:
        return _pending > 0


def wait_all(timeout=30.0):
    """Tests only: block until every job has finished, delivering results."""
    import time

    end = time.time() + timeout
    while time.time() < end:
        poll()
        if not busy():
            return True
        time.sleep(0.02)
    return False


def unregister():
    if bpy.app.timers.is_registered(poll):
        bpy.app.timers.unregister(poll)
