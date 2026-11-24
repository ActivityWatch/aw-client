"""Buckets registered for the request queue must survive disconnect()/connect()."""

import threading

import pytest
import requests
import tomlkit

from aw_client import ActivityWatchClient
from aw_client import client as client_module
from aw_client.config import default_config


@pytest.fixture
def client(monkeypatch, tmp_path):
    config = tomlkit.parse(default_config)
    monkeypatch.setattr(client_module, "load_config", lambda: config)
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: name)
    monkeypatch.setattr(client_module, "get_data_dir", lambda *args: str(tmp_path))
    monkeypatch.setattr(
        client_module, "load_local_server_api_key", lambda *args, **kwargs: None
    )
    monkeypatch.delenv("AW_PROFILE", raising=False)
    return ActivityWatchClient("reconnect-test", testing=True)


def replace_queue_while_offline(client, monkeypatch):
    attempted = threading.Event()

    def try_connect():
        attempted.set()
        return False

    queue = client.request_queue
    monkeypatch.setattr(queue, "_try_connect", try_connect)
    client.connect()
    try:
        # run() clears the stop flag on entry: wait until it has started before
        # stopping it, otherwise an immediate disconnect() can hang in join().
        assert attempted.wait(timeout=5)
    finally:
        queue.stop()
        queue.join(timeout=5)
    assert not queue.is_alive()
    client.disconnect()


def test_registered_buckets_survive_reconnect(client, monkeypatch):
    created = []
    monkeypatch.setattr(
        client, "_post", lambda endpoint, data: created.append(endpoint)
    )

    # Server is down: the queue thread cannot create the bucket yet.
    client.create_bucket("aw-watcher-test_host", "test", queued=True)
    replace_queue_while_offline(client, monkeypatch)

    # The replacement queue must still know which buckets to create, otherwise
    # every queued heartbeat 404s once the server comes up and is dropped.
    assert client.request_queue._try_connect() is True
    assert created == ["buckets/aw-watcher-test_host"]


@pytest.mark.parametrize("force", [False, True])
def test_deleted_bucket_is_not_recreated(client, monkeypatch, force):
    created = []
    deleted = []
    monkeypatch.setattr(
        client, "_post", lambda endpoint, data: created.append(endpoint)
    )
    monkeypatch.setattr(client, "_delete", lambda endpoint: deleted.append(endpoint))
    client.create_bucket("deleted", "test", queued=True)
    client.create_bucket("retained", "test", queued=True)
    client.create_bucket("deleted", "test", queued=True)

    client.delete_bucket("deleted", force=force)
    assert deleted == ["buckets/deleted" + ("?force=1" if force else "")]
    # Also exercise reconnect without replacing the worker.
    assert client.request_queue._try_connect() is True
    assert created == ["buckets/retained"]
    created.clear()
    replace_queue_while_offline(client, monkeypatch)
    assert client.request_queue._try_connect() is True
    assert created == ["buckets/retained"]


def test_failed_delete_keeps_registration(client, monkeypatch):
    created = []
    monkeypatch.setattr(
        client, "_post", lambda endpoint, data: created.append(endpoint)
    )

    def fail_delete(endpoint):
        raise requests.HTTPError("delete failed")

    monkeypatch.setattr(client, "_delete", fail_delete)
    client.create_bucket("retained", "test", queued=True)
    with pytest.raises(requests.HTTPError, match="delete failed"):
        client.delete_bucket("retained")

    replace_queue_while_offline(client, monkeypatch)
    assert client.request_queue._try_connect() is True
    assert created == ["buckets/retained"]
