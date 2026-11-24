"""Buckets registered for the request queue must survive disconnect()/connect()."""

import pytest
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
    monkeypatch.delenv("AW_PROFILE", raising=False)
    return ActivityWatchClient("reconnect-test", testing=True)


def test_registered_buckets_survive_reconnect(client, monkeypatch):
    created = []

    def create_bucket(bucket_id, event_type, queued=False):
        if queued:
            client.request_queue.register_bucket(bucket_id, event_type)
        else:
            created.append(bucket_id)

    monkeypatch.setattr(client, "create_bucket", create_bucket)
    # Server is down: the queue thread cannot create the bucket yet.
    monkeypatch.setattr(client.request_queue, "_try_connect", lambda: False)

    client.create_bucket("aw-watcher-test_host", "test", queued=True)
    client.connect()
    client.disconnect()

    # The replacement queue must still know which buckets to create, otherwise
    # every queued heartbeat 404s once the server comes up and is dropped.
    assert client.request_queue._try_connect() is True
    assert created == ["aw-watcher-test_host"]
