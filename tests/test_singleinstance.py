import errno
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from aw_client import singleinstance


def test_windows_fresh_name_acquires_descriptor(tmp_path, monkeypatch):
    monkeypatch.setattr(
        singleinstance, "sys", SimpleNamespace(platform="win32", exit=sys.exit)
    )
    monkeypatch.setattr(singleinstance, "get_cache_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(
        singleinstance,
        "msvcrt",
        SimpleNamespace(LK_NBLCK=1, locking=lambda fd, mode, count: None),
        raising=False,
    )
    instance = singleinstance.SingleInstance("fresh")
    try:
        assert hasattr(instance, "fd")
        os.fstat(instance.fd)
    finally:
        del instance


@pytest.mark.parametrize("lock_error", [errno.EACCES, errno.EBADF])
def test_windows_failed_lock_closes_descriptor(tmp_path, monkeypatch, lock_error):
    attempted = []

    def fail_lock(fd, mode, count):
        attempted.append(fd)
        raise OSError(lock_error, "lock failed")

    monkeypatch.setattr(
        singleinstance, "sys", SimpleNamespace(platform="win32", exit=sys.exit)
    )
    monkeypatch.setattr(singleinstance, "get_cache_dir", lambda name: str(tmp_path))
    monkeypatch.setattr(
        singleinstance,
        "msvcrt",
        SimpleNamespace(LK_NBLCK=1, locking=fail_lock),
        raising=False,
    )
    if lock_error == errno.EACCES:
        with pytest.raises(SystemExit) as exit_exc:
            singleinstance.SingleInstance("fresh")
        assert exit_exc.value.code == -1
    else:
        with pytest.raises(OSError) as exc:
            singleinstance.SingleInstance("fresh")
        assert exc.value.errno == lock_error
    assert len(attempted) == 1
    with pytest.raises(OSError) as closed_exc:
        os.fstat(attempted[0])
    assert closed_exc.value.errno == errno.EBADF


_CHILD = """
import sys
from pathlib import Path
from aw_client import singleinstance

cache, ready, mode = sys.argv[1:]
singleinstance.get_cache_dir = lambda name: cache
instance = singleinstance.SingleInstance("subprocess-lock")
if mode == "hold":
    Path(ready).write_text("acquired")
    sys.stdin.readline()
del instance
print("acquired and released")
"""


def _contender(tmp_path):
    return subprocess.run(
        [sys.executable, "-c", _CHILD, str(tmp_path), "unused", "try"],
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("abrupt_exit", [False, True], ids=["release", "kill"])
def test_process_exclusion_and_reacquisition(tmp_path, abrupt_exit):
    ready = tmp_path / "ready"
    owner = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(tmp_path), str(ready), "hold"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if owner.poll() is not None:
                pytest.fail(f"Owner exited before acquisition: {owner.communicate()}")
            if time.monotonic() >= deadline:
                pytest.fail("Owner did not acquire the lock within 10 seconds")
            time.sleep(0.02)
        denied = _contender(tmp_path)
        assert denied.returncode != 0, denied.stdout
        assert "Another instance is already running" in denied.stderr
        if abrupt_exit:
            owner.kill()
            owner.communicate(timeout=10)
        else:
            stdout, stderr = owner.communicate("release\n", timeout=10)
            assert owner.returncode == 0, stderr
            assert "acquired and released" in stdout
        acquired = _contender(tmp_path)
        assert acquired.returncode == 0, acquired.stderr
        assert "acquired and released" in acquired.stdout
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=10)


def test_existing_unowned_file_can_be_acquired(tmp_path):
    (tmp_path / "subprocess-lock").write_text("stale")
    for _ in range(2):
        acquired = _contender(tmp_path)
        assert acquired.returncode == 0, acquired.stderr
        assert "acquired and released" in acquired.stdout
