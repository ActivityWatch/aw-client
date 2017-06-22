import sys
import os
import errno
import tempfile
import unittest
import logging
from multiprocessing import Process

from aw_core.dirs import get_data_dir

logger = logging.getLogger(__name__)

class SingleInstance:
    """
        This code is taken and modified form the tendo python package
        (Python Software Foundation License Version 2)
        http://pythonhosted.org/tendo/_modules/tendo/singleton.html
    """
    def __init__(self, client_name):
        global sys
        self.lockfile = os.path.join(get_data_dir("client_locks"), client_name)
        logger.debug("SingleInstance lockfile: " + self.lockfile)
        if sys.platform == 'win32':
            try:
                # file already exists, we try to remove (in case previous execution was interrupted)
                if os.path.exists(self.lockfile):
                    os.unlink(self.lockfile)
                    self.fd =  os.open(self.lockfile, os.O_CREAT|os.O_EXCL|os.O_RDWR)
            except OSError as e:
                if e.errno == 13:
                    logger.error("Another instance is already running, quitting.")
                    sys.exit(-1)
                else:
                    raise e
        else: # non Windows
            import fcntl, sys
            self.fp = open(self.lockfile, 'w')
            try:
                fcntl.lockf(self.fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except IOError:
                logger.error("Another instance is already running, quitting.")
                sys.exit(-1)

    def __del__(self):
        global sys
        if sys.platform == 'win32':
            if hasattr(self, 'fd'):
                os.close(self.fd)
                os.unlink(self.lockfile)
