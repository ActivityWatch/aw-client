import sys
import os
import logging
import errno
import threading

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from aw_core.dirs import get_cache_dir

logger = logging.getLogger(__name__)

# POSIX record locks (lockf) belong to the process, and closing any descriptor
# of the file drops them. Same-name instances in one process therefore share one
# locked file, closed when the last of them goes away (#172).
_held: dict = {}  # lockfile -> [file, refcount]
_acquiring: set = set()
# Opening a file can trigger cyclic GC and re-enter SingleInstance.__del__.
_held_lock = threading.RLock()


def _after_fork_child():
    # lockf ownership is not inherited. Close copied descriptors before any new
    # child lock is acquired; closing one later would release that new lock.
    for fp, _ in _held.values():
        fp.close()
    _held.clear()
    _held_lock.release()


if sys.platform != "win32":
    # Fork only with a consistent registry, even if another thread is acquiring
    # or releasing a lock. Both processes resume with the mutex unlocked.
    os.register_at_fork(
        before=_held_lock.acquire,
        after_in_parent=_held_lock.release,
        after_in_child=_after_fork_child,
    )


class SingleInstance:
    """
    This code is taken and modified form the tendo python package
    (Python Software Foundation License Version 2)
    http://pythonhosted.org/tendo/_modules/tendo/singleton.html
    """

    def __init__(self, client_name):
        self.lockfile = os.path.join(get_cache_dir("client_locks"), client_name)
        logger.debug("SingleInstance lockfile: " + self.lockfile)
        if sys.platform == "win32":
            fd = os.open(self.lockfile, os.O_CREAT | os.O_RDWR)
            try:
                # Keep the file in place: ownership is the OS lock, not its existence.
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as e:
                os.close(fd)
                if e.errno == errno.EACCES:
                    logger.error("Another instance is already running, quitting.")
                    sys.exit(-1)
                else:
                    raise
            self.fd = fd
        else:  # non Windows
            with _held_lock:
                held = _held.get(self.lockfile)
                if held is not None:
                    held[1] += 1
                    self.fp = held[0]
                    return
                if self.lockfile in _acquiring:
                    raise RuntimeError(
                        f"Reentrant SingleInstance acquisition: {self.lockfile}"
                    )
                _acquiring.add(self.lockfile)
                pid = os.getpid()
                try:
                    fp = open(self.lockfile, "w")
                    try:
                        fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        if os.getpid() != pid:
                            # Forked after the lock was taken: it stayed with
                            # the parent, so the child must win its own.
                            fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        fp.close()
                    else:
                        _held[self.lockfile] = [fp, 1]
                        self.fp = fp
                        return
                finally:
                    _acquiring.remove(self.lockfile)
            # Logging handlers can run arbitrary code; do not hold the registry
            # lock while reporting a failed acquisition.
            logger.error("Another instance is already running, quitting.")
            sys.exit(-1)

    def __del__(self):
        if sys.platform == "win32":
            if hasattr(self, "fd"):
                os.close(self.fd)
        elif hasattr(self, "fp"):
            with _held_lock:
                held = _held.get(self.lockfile)
                if held is not None and held[0] is self.fp:
                    held[1] -= 1
                    if held[1] == 0:
                        del _held[self.lockfile]
                        self.fp.close()
