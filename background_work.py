"""Lower resource scheduling priority only for the current calculation thread."""

from contextlib import contextmanager
import ctypes
import sys


@contextmanager
def background_resources():
    kernel = None
    thread = None
    entered = False
    if sys.platform == "win32":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentThread.restype = wintypes.HANDLE
        kernel.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
        kernel.SetThreadPriority.restype = wintypes.BOOL
        thread = kernel.GetCurrentThread()
        entered = bool(kernel.SetThreadPriority(thread, 0x00010000))
    try:
        yield entered
    finally:
        if entered:
            kernel.SetThreadPriority(thread, 0x00020000)
