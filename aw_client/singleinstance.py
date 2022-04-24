import sys
import os
import logging

if sys.platform != "win32":
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
            try:
                # file already exists, we try to remove (in case previous execution was interrupted)
                if os.path.exists(self.lockfile):
                    os.unlink(self.lockfile)
                    self.fd = os.open(self.lockfile, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            except OSError as e:
                if e.errno == 13:
                    logger.error("Another instance is already running, quitting.")
                    sys.exit(-1)
                else:
                    raise e
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
                os.unlink(self.lockfile)
