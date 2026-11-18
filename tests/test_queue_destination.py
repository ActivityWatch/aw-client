"""Real durable queues must never follow a watcher to a different server."""

from pathlib import Path

import persistqueue
import pytest
import tomlkit

from aw_client import ActivityWatchClient
from aw_client import client as client_module
from aw_client.config import default_config
from aw_client.client import QueuedRequest

ENDPOINT = "buckets/synthetic/heartbeat?pulsetime=60"
PAYLOAD = {"data": {"app": "SYNTHETIC_SERVER_A"}}


@pytest.fixture
def config(monkeypatch, tmp_path):
    config = tomlkit.parse(default_config)
    monkeypatch.setattr(client_module, "load_config", lambda: config)
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: name)
    monkeypatch.setattr(client_module, "get_data_dir", lambda *args: str(tmp_path))
    monkeypatch.setattr(
        client_module, "load_local_server_api_key", lambda *a, **kw: None
    )
    monkeypatch.delenv("AW_PROFILE", raising=False)
    return config


def test_other_destination_cannot_dispatch_persisted_payload(config, monkeypatch):
    first = ActivityWatchClient("queue-test", port=15601)
    assert first.request_queue.add_request(ENDPOINT, PAYLOAD)
    second = ActivityWatchClient("queue-test", port=15602)
    posts = []
    monkeypatch.setattr(second, "_post", lambda *args: posts.append(args))
    monkeypatch.setattr(second.request_queue, "wait", lambda *args: None)
    second.request_queue._dispatch_request()
    assert posts == [], "server B must not receive server A's pending private events"
    assert first.request_queue._get_next() == QueuedRequest(ENDPOINT, PAYLOAD)
    assert (
        first.request_queue.persistqueue_path != second.request_queue.persistqueue_path
    )


@pytest.mark.parametrize(
    "first_url, second_url, same",
    [
        ("https://AW.example/prefix/", "https://aw.example:443/prefix", True),
        ("http://aw.example", "http://aw.example:80/", True),
        ("https://[0:0:0:0:0:0:0:1]/", "https://[::1]:443", True),
        ("https://aw.example/a", "https://aw.example/b", False),
        ("http://aw.example:80", "https://aw.example:80", False),
        ("https://aw.example/Case", "https://aw.example/case", False),
    ],
)
def test_effective_url_identity(config, first_url, second_url, same):
    config["server"]["baseurl"] = first_url
    first = ActivityWatchClient("queue-test")
    config["server"]["baseurl"] = second_url
    second = ActivityWatchClient("queue-test")
    assert (
        first.request_queue.persistqueue_path == second.request_queue.persistqueue_path
    ) == same
    name = Path(first.request_queue.persistqueue_path).name
    assert "example" not in name
    assert ":" not in name


def test_same_destination_reopens_durable_queue(config):
    first = ActivityWatchClient("queue-test", port=15601)
    assert first.request_queue.add_request(ENDPOINT, PAYLOAD)
    second = ActivityWatchClient("queue-test", port=15601)
    assert second.request_queue._get_next() == QueuedRequest(ENDPOINT, PAYLOAD)


def test_profiles_remain_isolated(config):
    first = ActivityWatchClient("queue-test", port=15601, profile="default")
    second = ActivityWatchClient("queue-test", port=15601, profile="research")
    assert (
        first.request_queue.persistqueue_path != second.request_queue.persistqueue_path
    )


def _legacy_queue(tmp_path):
    legacy_path = tmp_path / "queued" / "queue-test.v1.persistqueue"
    legacy = persistqueue.FIFOSQLiteQueue(str(legacy_path), auto_commit=False)
    legacy.put(QueuedRequest(ENDPOINT, PAYLOAD))
    return legacy_path


def test_default_local_keeps_legacy_backlog(config, tmp_path):
    legacy_path = _legacy_queue(tmp_path)
    config["server"]["baseurl"] = "http://127.0.0.1:5600/"
    local = ActivityWatchClient("queue-test")
    assert local.request_queue.persistqueue_path == str(legacy_path)
    assert local.request_queue._get_next() == QueuedRequest(ENDPOINT, PAYLOAD)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "localhost", "port": 5600},
        {"host": "127.0.0.1", "port": 15601},
        {"host": "aw.example", "port": 5600},
    ],
)
def test_first_nondefault_destination_adopts_legacy_backlog(
    config, tmp_path, caplog, kwargs
):
    legacy_path = _legacy_queue(tmp_path)
    client = ActivityWatchClient("queue-test", **kwargs)
    assert client.request_queue.persistqueue_path != str(legacy_path)
    assert client.request_queue._get_next() == QueuedRequest(ENDPOINT, PAYLOAD)
    assert not legacy_path.exists(), "adopted by rename, not copied"
    assert all(r.levelno < 30 for r in caplog.records), "no warning after adoption"


@pytest.mark.parametrize("legacy_backlog", [False, True])
def test_nondefault_client_never_adopts_default_live_queue(
    config, tmp_path, legacy_backlog
):
    """After the default client has opened the legacy path it is a live queue."""
    if legacy_backlog:
        _legacy_queue(tmp_path)
    local = ActivityWatchClient("queue-test", port=5600)
    assert local.request_queue.add_request(ENDPOINT, {"data": {"app": "LOCAL"}})
    remote = ActivityWatchClient("queue-test", host="aw.example", port=5600)
    assert remote.request_queue.persistqueue_path != (
        local.request_queue.persistqueue_path
    )
    assert remote.request_queue._get_next() is None
    assert Path(local.request_queue.persistqueue_path).exists()
    reopened = ActivityWatchClient("queue-test", port=5600)
    pending = [reopened.request_queue._get_next()]
    if legacy_backlog:
        reopened.request_queue._task_done()
        pending.append(reopened.request_queue._get_next())
    assert QueuedRequest(ENDPOINT, {"data": {"app": "LOCAL"}}) in pending


def test_legacy_backlog_goes_to_exactly_one_destination(config, tmp_path):
    _legacy_queue(tmp_path)
    first = ActivityWatchClient("queue-test", port=15601)
    second = ActivityWatchClient("queue-test", port=15602)
    assert first.request_queue._get_next() == QueuedRequest(ENDPOINT, PAYLOAD)
    assert second.request_queue._get_next() is None


def test_adoption_is_one_time_and_restart_keeps_backlog(config, tmp_path):
    _legacy_queue(tmp_path)
    ActivityWatchClient("queue-test", port=15601)
    again = ActivityWatchClient("queue-test", port=15601)
    assert again.request_queue._get_next() == QueuedRequest(ENDPOINT, PAYLOAD)


def test_failed_adoption_leaves_legacy_in_place_and_warns(
    config, tmp_path, monkeypatch, caplog
):
    legacy_path = _legacy_queue(tmp_path)

    def refuse(*args):
        raise PermissionError("synthetic")

    monkeypatch.setattr(client_module.os, "rename", refuse)
    client = ActivityWatchClient("queue-test", port=15601)
    assert client.request_queue._get_next() is None
    assert legacy_path.is_dir()
    assert "could not adopt" in caplog.text.lower()


def test_profiles_do_not_adopt_each_others_legacy_backlog(config, tmp_path):
    legacy_path = _legacy_queue(tmp_path)
    other = ActivityWatchClient("queue-test", port=15601, profile="research")
    assert other.request_queue._get_next() is None
    assert legacy_path.is_dir()


def test_explicit_and_configured_local_destination_share_queue(config):
    explicit = ActivityWatchClient("queue-test", host="127.0.0.1", port=5600)
    config["server"]["baseurl"] = "http://127.0.0.1:5600/"
    configured = ActivityWatchClient("queue-test")
    assert (
        explicit.request_queue.persistqueue_path
        == configured.request_queue.persistqueue_path
    )


def test_default_testing_profile_preserves_legacy_filename(config):
    client = ActivityWatchClient("queue-test", profile="testing")
    assert (
        Path(client.request_queue.persistqueue_path).name
        == "queue-test-testing.v1.persistqueue"
    )


def test_reconnect_preserves_destination_backlog(config, monkeypatch):
    client = ActivityWatchClient("queue-test", port=15601, profile="research")
    queue = client.request_queue
    path = queue.persistqueue_path
    monkeypatch.setattr(queue, "_try_connect", lambda: False)
    queue._attempt_reconnect_interval = 1
    client.connect()
    assert queue.add_request(ENDPOINT, PAYLOAD)
    client.disconnect()
    assert client.request_queue.persistqueue_path == path
    assert client.request_queue._get_next() == QueuedRequest(ENDPOINT, PAYLOAD)


def test_explicit_persistence_path_remains_caller_owned(config, tmp_path):
    first = ActivityWatchClient("queue-test", port=15601)
    path = str(tmp_path / "explicit-queue")
    queue = client_module.RequestQueue(first, persistqueue_path=path)
    assert queue.persistqueue_path == path
    assert queue.add_request(ENDPOINT, PAYLOAD)
    reopened = client_module.RequestQueue(first, persistqueue_path=path)
    assert reopened._get_next() == QueuedRequest(ENDPOINT, PAYLOAD)
