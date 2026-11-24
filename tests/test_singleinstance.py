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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX lock semantics")
def test_same_process_duplicate_keeps_owner_lock(tmp_path, monkeypatch):
    """#172: lockf locks belong to the process, so collecting a second same-name
    instance closed its file and released the first instance's lock."""
    import gc

    monkeypatch.setattr(singleinstance, "get_cache_dir", lambda name: str(tmp_path))
    owner = singleinstance.SingleInstance("subprocess-lock")
    duplicate = singleinstance.SingleInstance("subprocess-lock")
    del duplicate
    gc.collect()
    denied = _contender(tmp_path)
    assert denied.returncode != 0, denied.stdout
    assert "Another instance is already running" in denied.stderr
    del owner
    gc.collect()
    acquired = _contender(tmp_path)
    assert acquired.returncode == 0, acquired.stderr


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_forked_client_must_acquire_its_own_lock(tmp_path):
    script = """
import os
import sys
from aw_client import singleinstance

singleinstance.get_cache_dir = lambda name: sys.argv[1]
owner = singleinstance.SingleInstance("subprocess-lock")
pid = os.fork()
if pid == 0:
    try:
        duplicate = singleinstance.SingleInstance("subprocess-lock")
    except SystemExit:
        os._exit(0)
    os._exit(1)
_, status = os.waitpid(pid, 0)
assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, "forked child bypassed the OS lock"
del owner
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_concurrent_clients_keep_owner_lock(tmp_path, monkeypatch):
    # Skip in the body rather than via skipif: mypy narrows on this check,
    # keeping the POSIX-only fcntl references out of the Windows typecheck.
    if sys.platform == "win32":
        pytest.skip("POSIX lock semantics")
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    monkeypatch.setattr(singleinstance, "get_cache_dir", lambda name: str(tmp_path))
    lock_entered = Event()
    release_lock = Event()
    second_open = Event()
    real_lockf = singleinstance.fcntl.lockf
    real_open = open

    def delayed_lockf(fp, mode):
        if not lock_entered.is_set():
            lock_entered.set()
            assert release_lock.wait(5)
        return real_lockf(fp, mode)

    def tracked_open(*args, **kwargs):
        if lock_entered.is_set():
            second_open.set()
        return real_open(*args, **kwargs)

    monkeypatch.setattr(singleinstance.fcntl, "lockf", delayed_lockf)
    monkeypatch.setattr(singleinstance, "open", tracked_open, raising=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(singleinstance.SingleInstance, "subprocess-lock")
        assert lock_entered.wait(5)
        second = pool.submit(singleinstance.SingleInstance, "subprocess-lock")
        try:
            # The unfixed constructor opens a second descriptor while the first
            # is acquiring its lock. A serialized constructor waits instead.
            second_open.wait(0.2)
        finally:
            release_lock.set()
        owner = first.result(timeout=5)
        duplicate = second.result(timeout=5)
    # Futures retain their return values, so drop them before testing collection.
    del first, second, duplicate
    denied = _contender(tmp_path)
    assert denied.returncode != 0, denied.stdout
    del owner
    acquired = _contender(tmp_path)
    assert acquired.returncode == 0, acquired.stderr


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_forked_client_reacquires_after_parent_release(tmp_path):
    script = """
import os
import subprocess
import sys
from aw_client import singleinstance

singleinstance.get_cache_dir = lambda name: sys.argv[1]
owner = singleinstance.SingleInstance("subprocess-lock")
read_fd, write_fd = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(write_fd)
    os.read(read_fd, 1)
    os.close(read_fd)
    duplicate = singleinstance.SingleInstance("subprocess-lock")
    # Collecting the inherited object must not close a descriptor of the file
    # after this child has acquired its own process-owned lock.
    del owner
    contender = subprocess.run(
        [sys.executable, "-c", sys.argv[2], sys.argv[1], "unused", "try"],
        capture_output=True, text=True, timeout=5,
    )
    if contender.returncode == 0:
        os._exit(1)
    del duplicate
    contender = subprocess.run(
        [sys.executable, "-c", sys.argv[2], sys.argv[1], "unused", "try"],
        capture_output=True, text=True, timeout=5,
    )
    os._exit(0 if contender.returncode == 0 else 2)
os.close(read_fd)
del owner
os.write(write_fd, b"x")
os.close(write_fd)
_, status = os.waitpid(pid, 0)
assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), _CHILD],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
