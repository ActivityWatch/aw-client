"""Real socket/SQLite regressions; the test peer always has bounded cleanup."""

import socket
import threading
from contextlib import contextmanager
from typing import Any, Tuple

import pytest
import requests
import tomlkit

from aw_client import ActivityWatchClient
from aw_client import client as client_module
from aw_client.config import default_config


@pytest.fixture
def isolated_client(monkeypatch, tmp_path):
    monkeypatch.setattr(
        client_module, "load_config", lambda: tomlkit.parse(default_config)
    )
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: name)
    monkeypatch.setattr(client_module, "get_data_dir", lambda *args: str(tmp_path))
    monkeypatch.setattr(
        client_module, "load_local_server_api_key", lambda *a, **kw: None
    )


@contextmanager
def silent_peer():
    """Accept a request but withhold all response bytes until released."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    received = threading.Event()
    release = threading.Event()

    def serve():
        while not release.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(0.1)
                try:
                    if conn.recv(65536):
                        received.set()
                except socket.timeout:
                    pass
                release.wait(5)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], received
    finally:
        release.set()
        thread.join(2)
        listener.close()
        assert not thread.is_alive()


def background_call(call):
    done = threading.Event()
    errors = []

    def run():
        try:
            call()
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, done, errors


def test_startup_deadline_bounds_silent_peer(isolated_client):
    with silent_peer() as (port, received):
        client = ActivityWatchClient("startup", host="127.0.0.1", port=port)
        thread, done, errors = background_call(
            lambda: client.wait_for_start(timeout=0.1)
        )
        try:
            assert received.wait(2), "test peer did not receive the startup request"
            assert done.wait(0.8), "startup remained blocked beyond its deadline"
            assert len(errors) == 1
            assert "did not start in time" in str(errors[0])
        finally:
            client.request_queue.stop()
    thread.join(2)


def test_disconnect_bounds_inflight_and_retains_sqlite_request(isolated_client):
    with silent_peer() as (port, received):
        client = ActivityWatchClient("shutdown", host="127.0.0.1", port=port)
        # Set the configurable policy after construction so the pre-fix test
        # reaches the actual unbounded request rather than a missing argument.
        client.request_timeout = 0.1
        queue = client.request_queue
        payload = {"timestamp": "2026-10-28T00:00:00Z", "duration": 1, "data": {"x": 1}}
        assert queue.add_request("buckets/test/heartbeat", payload)
        client.connect()
        assert received.wait(2), "queue did not send its persisted request"
        thread, done, errors = background_call(client.disconnect)
        try:
            assert done.wait(0.8), "disconnect remained blocked on an in-flight POST"
            assert not errors
            assert not queue.is_alive()
            # disconnect constructs a new queue against the same real database.
            pending = client.request_queue._get_next()
            assert pending is not None
            assert pending.data == payload
        finally:
            queue.stop()
    thread.join(2)
    queue.join(2)


def test_stop_before_worker_run_is_not_lost(isolated_client):
    client = ActivityWatchClient("early-stop")
    queue = client.request_queue
    entered = threading.Event()
    release = threading.Event()
    original_run = queue.run

    def delayed_run():
        entered.set()
        release.wait(2)
        original_run()

    queue.run = delayed_run  # type: ignore[method-assign]
    queue.start()
    assert entered.wait(2)
    queue.stop()
    release.set()
    try:
        queue.join(0.8)
        assert (
            not queue.is_alive()
        ), "run erased a stop requested during thread bootstrap"
    finally:
        queue.stop()
        queue.join(2)


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_transport_timeout_default_and_override(isolated_client, monkeypatch, method):
    seen = []

    def reply(*args, **kwargs):
        seen.append(kwargs["timeout"])
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(client_module.req, method, reply)
    default_client = ActivityWatchClient("default-policy")
    assert default_client.request_timeout == 10
    client = ActivityWatchClient("policy", request_timeout=3)
    request = getattr(client, "_" + method)
    args: Tuple[Any, ...] = ("info", {}) if method == "post" else ("info",)
    request(*args)
    request(*args, timeout=60)
    request(*args, timeout=None)
    assert seen == [3, 60, 3]


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_reject_unbounded_or_invalid_policy(isolated_client, timeout):
    with pytest.raises(ValueError, match="finite and greater than zero"):
        ActivityWatchClient("invalid-policy", request_timeout=timeout)


@pytest.mark.parametrize(
    "operation", ["query", "export_all", "export_bucket", "import_bucket"]
)
def test_long_operation_override(isolated_client, monkeypatch, operation):
    seen = []

    def reply(*args, **kwargs):
        seen.append(kwargs["timeout"])
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(client_module.req, "get", reply)
    monkeypatch.setattr(client_module.req, "post", reply)
    client = ActivityWatchClient("long-operation")
    args: Tuple[Any, ...] = {
        "query": ("RETURN [];", []),
        "export_all": (),
        "export_bucket": ("test",),
        "import_bucket": ({"id": "test"},),
    }[operation]
    getattr(client, operation)(*args, timeout=120)
    assert seen == [120]


def test_startup_caps_each_attempt_to_remaining_budget(isolated_client, monkeypatch):
    now = [0.0]
    attempts = []
    monkeypatch.setattr(client_module, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        client_module, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    client = ActivityWatchClient("budget", request_timeout=2)

    def timeout_info(*, timeout):
        attempts.append((now[0], timeout))
        assert 0 < timeout <= min(2, (1 - now[0]) / 2)
        now[0] += timeout
        raise requests.ReadTimeout("silent peer")

    monkeypatch.setattr(client, "get_info", timeout_info)
    with pytest.raises(Exception, match="did not start in time"):
        client.wait_for_start(timeout=1)
    assert now[0] == pytest.approx(1)
    assert len(attempts) >= 2


def test_disconnect_during_bucket_registration(isolated_client):
    with silent_peer() as (port, received):
        client = ActivityWatchClient(
            "bucket-shutdown", host="127.0.0.1", port=port, request_timeout=0.1
        )
        queue = client.request_queue
        queue.register_bucket("first", "test")
        queue.register_bucket("second", "test")
        client.connect()
        assert received.wait(2)
        thread, done, errors = background_call(client.disconnect)
        try:
            assert done.wait(0.8), "disconnect remained blocked creating buckets"
            assert not errors
            assert not queue.is_alive()
            assert not client.request_queue.is_alive()
        finally:
            queue.stop()
    thread.join(2)
    queue.join(2)


def test_stop_prevents_further_bucket_requests(isolated_client, monkeypatch):
    client = ActivityWatchClient("stop-buckets")
    queue = client.request_queue
    queue.register_bucket("first", "test")
    queue.register_bucket("second", "test")
    calls = []

    def create_bucket(bucket_id, event_type):
        calls.append(bucket_id)
        queue.stop()

    monkeypatch.setattr(client, "create_bucket", create_bucket)
    queue._create_buckets()
    assert calls == ["first"]


def test_cli_long_operation_policy(monkeypatch):
    from click.testing import CliRunner
    from aw_client.cli import main

    policies = []

    class Client:
        def __init__(self, **kwargs):
            policies.append(kwargs["request_timeout"])

        def get_buckets(self):
            return {}

    monkeypatch.setattr("aw_client.ActivityWatchClient", Client)
    result = CliRunner().invoke(main, ["--request-timeout", "120", "buckets"])
    assert result.exit_code == 0, result.output
    assert policies == [120]
