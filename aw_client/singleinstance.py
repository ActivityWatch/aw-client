import sys
import os
import logging
import errno

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from aw_core.dirs import get_cache_dir

logger = logging.getLogger(__name__)


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
            self.fp = open(self.lockfile, "w")
            try:
                fcntl.lockf(self.fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                logger.error("Another instance is already running, quitting.")
                sys.exit(-1)

    def __del__(self):
        if sys.platform == "win32":
            if hasattr(self, "fd"):
                os.close(self.fd)
