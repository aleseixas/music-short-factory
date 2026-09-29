"""Kernel-owned file locks: process death releases ownership without deleting files."""
from contextlib import contextmanager
import os
from pathlib import Path
import time


@contextmanager
def process_lock(path: Path, *, timeout: float = 10):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the inode permanently: unlinking a released lock races existing waiters.
    handle = path.open("a+b")
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Active process holds lock: {path.name}")
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
