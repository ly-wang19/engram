"""Exclusive advisory locks on an open lock file, on POSIX and Windows alike.

Every lock in Engram guards a dedicated `.lock` file, so locking its first byte stands for the whole
file. POSIX uses `fcntl.flock`; Windows has no fcntl, and before this module each call site either
skipped locking there or failed to import.
"""
from __future__ import annotations

import os
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt


def lock(fd: int, blocking: bool = True) -> bool:
    """Take the exclusive lock on `fd`. With `blocking=False`, return False instead of waiting when
    another process holds it."""
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError:
            if blocking:
                raise
            return False
        return True
    # LK_LOCK gives up after about ten seconds; waiting for as long as it takes means polling LK_NBLCK.
    while True:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            if not blocking:
                return False
            time.sleep(0.05)


def unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass  # closing the file releases it anyway
