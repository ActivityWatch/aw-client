"""Graceful shutdown persists observed intervals, using real offline SQLite queues."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import tomlkit

from aw_core import Event
from aw_client import ActivityWatchClient
from aw_client import client as client_module
from aw_client.client import RequestQueue
from aw_client.config import default_config


@pytest.fixture
def client(monkeypatch, tmp_path):
    for kind in ("CONFIG", "DATA", "CACHE"):
        monkeypatch.setenv(f"XDG_{kind}_HOME", str(tmp_path / kind.lower()))
    # XDG roots are ignored on macOS/Windows; isolate the directory/config
    # boundary there too, without substituting the real SQLite queue.
    monkeypatch.setattr(client_module, "get_data_dir", lambda app: str(tmp_path))
    monkeypatch.setattr(
        client_module, "load_config", lambda: tomlkit.parse(default_config)
    )
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: object())
    monkeypatch.setattr(RequestQueue, "_try_connect", lambda self: False)
    c = ActivityWatchClient("disconnect-tail-test", profile="default")
    c.connect()
    yield c
    if c.request_queue.is_alive():
        c.request_queue.stop()
        c.request_queue.join(timeout=2)
        assert not c.request_queue.is_alive()


def heartbeat(client, seconds, bucket="test", label="same", pulsetime=10):
    client.heartbeat(
        bucket,
        Event(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc)
            + timedelta(seconds=seconds),
            data={"label": label},
        ),
        pulsetime=pulsetime,
        queued=True,
        commit_interval=10,
    )


def reopened_requests(client):
    """Read via a fresh client, not the old object's heartbeat cache."""
    reopened = ActivityWatchClient(client.client_name, profile="default")
    queue = reopened.request_queue
    result = []
    while queue._persistqueue.qsize():
        result.append(queue._get_next())
        queue._task_done()
    return result


def test_disconnect_persists_observed_tail(client):
    heartbeat(client, 0)
    heartbeat(client, 5)
    assert client.last_heartbeat["test"].duration.total_seconds() == 5
    client.disconnect()

    requests = reopened_requests(client)
    assert len(requests) == 1
    assert requests[0].endpoint == "buckets/test/heartbeat?pulsetime=10"
    assert requests[0].data["duration"] == 5
    assert requests[0].data["timestamp"] == "2026-01-01T00:00:00+00:00"


def test_multiple_buckets_and_repeated_disconnect(client):
    heartbeat(client, 0, bucket="a", pulsetime=11)
    heartbeat(client, 5, bucket="a", pulsetime=11)
    heartbeat(client, 1, bucket="b", pulsetime=20)
    heartbeat(client, 8, bucket="b", pulsetime=20)
    client.disconnect()
    client.disconnect()
    requests = reopened_requests(client)
    assert [(r.endpoint, r.data["duration"]) for r in requests] == [
        ("buckets/a/heartbeat?pulsetime=11", 5),
        ("buckets/b/heartbeat?pulsetime=20", 7),
    ]
    assert not client.last_heartbeat


def test_data_transition_preserves_original_pulsetime(client):
    heartbeat(client, 0, label="old", pulsetime=10)
    heartbeat(client, 5, label="old", pulsetime=10)
    heartbeat(client, 6, label="new", pulsetime=30)
    heartbeat(client, 9, label="new", pulsetime=30)
    client.disconnect()
    requests = reopened_requests(client)
    assert [
        (r.endpoint, r.data["data"]["label"], r.data["duration"]) for r in requests
    ] == [
        ("buckets/test/heartbeat?pulsetime=10", "old", 5),
        ("buckets/test/heartbeat?pulsetime=30", "new", 3),
    ]


def test_reconnect_starts_new_tail_without_requeueing_previous_one(client):
    heartbeat(client, 0)
    heartbeat(client, 5)
    client.disconnect()
    client.connect()
    heartbeat(client, 100)
    heartbeat(client, 103)
    client.disconnect()
    requests = reopened_requests(client)
    assert [r.data["duration"] for r in requests] == [5, 3]
    assert requests[1].data["timestamp"] == "2026-01-01T00:01:40+00:00"


def test_context_manager_exit_persists_tail(client):
    client.disconnect()
    with client:
        heartbeat(client, 0)
        heartbeat(client, 5)
    assert [r.data["duration"] for r in reopened_requests(client)] == [5]


@pytest.mark.parametrize("error_type", [OSError, sqlite3.OperationalError])
def test_failed_shutdown_retains_order_and_retries_without_duplicates(
    client, monkeypatch, error_type
):
    queue = client.request_queue
    real_put = queue._persistqueue.put
    failing = True

    def put(request):
        if failing:
            raise error_type("synthetic full disk")
        return real_put(request)

    monkeypatch.setattr(queue._persistqueue, "put", put)
    heartbeat(client, 0, label="old", pulsetime=10)
    heartbeat(client, 5, label="old", pulsetime=10)
    heartbeat(client, 6, label="middle", pulsetime=20)
    heartbeat(client, 7, label="new", pulsetime=30)
    heartbeat(client, 9, label="new", pulsetime=30)

    with pytest.raises(OSError, match="retry disconnect"):
        client.disconnect()
    assert not queue.is_alive()
    assert queue._persistqueue.qsize() == 0
    assert len(client._unqueued_heartbeats["test"]) == 2
    assert client.last_heartbeat["test"].duration.total_seconds() == 2

    failing = False
    client.disconnect()
    client.disconnect()
    requests = reopened_requests(client)
    assert [
        (r.endpoint, r.data["data"]["label"], r.data["duration"]) for r in requests
    ] == [
        ("buckets/test/heartbeat?pulsetime=10", "old", 5),
        ("buckets/test/heartbeat?pulsetime=20", "middle", 0),
        ("buckets/test/heartbeat?pulsetime=30", "new", 2),
    ]


def test_partial_shutdown_failure_only_retries_unwritten_intervals(client, monkeypatch):
    queue = client.request_queue
    real_put = queue._persistqueue.put
    attempts = 0

    def put(request):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise sqlite3.OperationalError("synthetic full disk")
        return real_put(request)

    monkeypatch.setattr(queue._persistqueue, "put", put)
    heartbeat(client, 0, bucket="a")
    heartbeat(client, 5, bucket="a")
    heartbeat(client, 0, bucket="b")
    heartbeat(client, 7, bucket="b")
    with pytest.raises(OSError, match="retry disconnect"):
        client.disconnect()
    assert list(client.last_heartbeat) == ["b"]
    client.disconnect()
    requests = reopened_requests(client)
    assert [(r.endpoint, r.data["duration"]) for r in requests] == [
        ("buckets/a/heartbeat?pulsetime=10", 5),
        ("buckets/b/heartbeat?pulsetime=10", 7),
    ]


def test_reconnect_after_failed_disconnect_can_retry_pending_tail(client, monkeypatch):
    heartbeat(client, 0)
    heartbeat(client, 5)
    with monkeypatch.context() as failing:
        failing.setattr(client.request_queue, "add_request", lambda *args: False)
        with pytest.raises(OSError, match="retry disconnect"):
            client.disconnect()
    client.connect()
    client.disconnect()
    assert [r.data["duration"] for r in reopened_requests(client)] == [5]


def test_partial_pending_recovery_keeps_only_unwritten_suffix(client, monkeypatch):
    queue = client.request_queue
    real_put = queue._persistqueue.put
    failing = True
    accepted = 0

    def put(request):
        nonlocal accepted
        if failing or accepted == 1:
            raise sqlite3.OperationalError("synthetic full disk")
        accepted += 1
        return real_put(request)

    monkeypatch.setattr(queue._persistqueue, "put", put)
    heartbeat(client, 0, label="old", pulsetime=10)
    heartbeat(client, 1, label="middle", pulsetime=20)
    heartbeat(client, 2, label="new", pulsetime=30)
    failing = False
    with pytest.raises(OSError, match="retry disconnect"):
        client.disconnect()
    assert len(client._unqueued_heartbeats["test"]) == 1
    # Recovery now uses the recreated worker and the same real SQLite file.
    client.disconnect()
    requests = reopened_requests(client)
    assert [r.data["data"]["label"] for r in requests] == ["old", "middle", "new"]


def test_commit_boundary_tail_and_changed_pulsetime(client):
    for t in (0, 5, 10):
        heartbeat(client, t)
    # The committed interval ends at 10; the observed tail starts there.
    heartbeat(client, 13, pulsetime=30)
    client.disconnect()
    requests = reopened_requests(client)
    assert [(r.endpoint, r.data["duration"]) for r in requests] == [
        ("buckets/test/heartbeat?pulsetime=10", 10),
        ("buckets/test/heartbeat?pulsetime=30", 3),
    ]


def test_disconnect_before_connect_persists_single_observation(client):
    client.disconnect()
    with pytest.warns(UserWarning, match="connect"):
        heartbeat(client, 0)
    client.disconnect()
    requests = reopened_requests(client)
    assert len(requests) == 1
    assert requests[0].data["duration"] == 0
    client.disconnect()
    assert not reopened_requests(client)


def test_unexpected_enqueue_error_still_allows_reconnect_and_retry(client, monkeypatch):
    heartbeat(client, 0)
    heartbeat(client, 5)

    def broken_enqueue(*args):
        raise RuntimeError("synthetic unexpected enqueue error")

    with monkeypatch.context() as failing:
        failing.setattr(client.request_queue, "add_request", broken_enqueue)
        with pytest.raises(RuntimeError, match="unexpected enqueue"):
            client.disconnect()
    client.connect()
    client.disconnect()
    assert [r.data["duration"] for r in reopened_requests(client)] == [5]
