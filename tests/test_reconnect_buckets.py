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


def test_delete_of_missing_bucket_forgets_registration(client, monkeypatch):
    created = []
    monkeypatch.setattr(
        client, "_post", lambda endpoint, data: created.append(endpoint)
    )

    def not_found(endpoint):
        response = requests.Response()
        response.status_code = 404
        raise requests.HTTPError("404 Not Found", response=response)

    monkeypatch.setattr(client, "_delete", not_found)
    client.create_bucket("gone", "test", queued=True)
    client.create_bucket("retained", "test", queued=True)
    # The error still reaches the caller, but the bucket is already absent,
    # so reconnect must not recreate it.
    with pytest.raises(requests.HTTPError):
        client.delete_bucket("gone")

    assert client.request_queue._try_connect() is True
    assert created == ["buckets/retained"]


def test_delete_waits_for_reconnect_bucket_creation(client, monkeypatch):
    log = []
    creating = threading.Event()
    release = threading.Event()

    def post(endpoint, data):
        log.append(("create", endpoint))
        if endpoint == "buckets/retained":
            creating.set()
            assert release.wait(timeout=5)

    monkeypatch.setattr(client, "_post", post)
    monkeypatch.setattr(
        client, "_delete", lambda endpoint: log.append(("delete", endpoint))
    )
    client.create_bucket("retained", "test", queued=True)
    client.create_bucket("deleted", "test", queued=True)

    reconnect = threading.Thread(target=client.request_queue._try_connect)
    reconnect.start()
    assert creating.wait(timeout=5)
    delete = threading.Thread(target=client.delete_bucket, args=("deleted",))
    delete.start()
    # The DELETE must not run while reconnect is still creating buckets,
    # or reconnect recreates the bucket right after it was deleted.
    delete.join(timeout=0.3)
    assert delete.is_alive()
    release.set()
    reconnect.join(timeout=5)
    delete.join(timeout=5)
    assert not reconnect.is_alive() and not delete.is_alive()

    assert log == [
        ("create", "buckets/retained"),
        ("create", "buckets/deleted"),
        ("delete", "buckets/deleted"),
    ]


def test_stalled_delete_does_not_block_disconnect(client, monkeypatch):
    deleting = threading.Event()
    release = threading.Event()

    def delete(endpoint):
        deleting.set()
        assert release.wait(timeout=10)

    monkeypatch.setattr(client, "_post", lambda endpoint, data: None)
    monkeypatch.setattr(client, "_delete", delete)
    client.create_bucket("deleted", "test", queued=True)

    deleter = threading.Thread(target=client.delete_bucket, args=("deleted",))
    deleter.start()
    try:
        assert deleting.wait(timeout=5)
        # Wait until run() has started (it clears the stop flag on entry) and
        # is about to wait for the bucket lock held by the stalled DELETE;
        # stop() must still end it.
        queue = client.request_queue
        connecting = threading.Event()
        try_connect = queue._try_connect

        def signal_then_connect():
            connecting.set()
            return try_connect()

        monkeypatch.setattr(queue, "_try_connect", signal_then_connect)
        client.connect()
        assert connecting.wait(timeout=5)
        disconnect = threading.Thread(target=client.disconnect, daemon=True)
        disconnect.start()
        disconnect.join(timeout=3)
        assert not disconnect.is_alive()
    finally:
        release.set()
        deleter.join(timeout=5)
    assert not deleter.is_alive()


def test_delete_in_flight_is_not_undone_by_queue_replacement(client, monkeypatch):
    created = []
    deleting = threading.Event()
    release = threading.Event()

    def delete(endpoint):
        deleting.set()
        assert release.wait(timeout=10)

    monkeypatch.setattr(
        client, "_post", lambda endpoint, data: created.append(endpoint)
    )
    monkeypatch.setattr(client, "_delete", delete)
    client.create_bucket("deleted", "test", queued=True)
    client.create_bucket("retained", "test", queued=True)

    deleter = threading.Thread(target=client.delete_bucket, args=("deleted",))
    deleter.start()
    try:
        assert deleting.wait(timeout=5)
        # Replace the worker while the DELETE is still in flight. The new
        # worker must share the registrations, not hold a copy taken now.
        old_queue = client.request_queue
        replace_queue_while_offline(client, monkeypatch)
        assert client.request_queue is not old_queue
        assert client.request_queue._registered_buckets is (
            old_queue._registered_buckets
        )
    finally:
        release.set()
        deleter.join(timeout=5)
    assert not deleter.is_alive()

    assert client.request_queue._try_connect() is True
    assert created == ["buckets/retained"]


def test_unregister_keeps_a_concurrent_registration(client, monkeypatch):
    client.create_bucket("deleted", "test", queued=True)
    queue = client.request_queue
    registered = queue._registered_buckets

    class RegisterDuringScan(list):
        # Register another bucket right after unregister_bucket() has scanned
        # the list, as a concurrent create_bucket(queued=True) could.
        fired = False

        def __iter__(self):
            yield from super().__iter__()
            if not self.fired:
                self.fired = True
                client.create_bucket("concurrent", "test", queued=True)

    shared = RegisterDuringScan(registered)
    monkeypatch.setattr(client, "_registered_buckets", shared)
    queue.unregister_bucket("deleted")
    assert [bucket.id for bucket in client._registered_buckets] == ["concurrent"]


def test_connected_registration_and_delete_are_serialized(client, monkeypatch):
    posting = threading.Event()
    release_post = threading.Event()
    deleting = threading.Event()
    operations = []
    errors = []

    def post(endpoint, data):
        posting.set()
        assert release_post.wait(timeout=5)
        operations.append(("create", endpoint))

    def delete(endpoint):
        operations.append(("delete", endpoint))

    def register():
        try:
            client.create_bucket("shared", "test", queued=True)
        except Exception as error:
            errors.append(error)

    def delete_bucket():
        try:
            deleting.set()
            client.delete_bucket("shared")
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(client, "_post", post)
    monkeypatch.setattr(client, "_delete", delete)
    client.request_queue.connected = True
    creator = threading.Thread(target=register, daemon=True)
    deleter = threading.Thread(target=delete_bucket, daemon=True)
    creator.start()
    try:
        assert posting.wait(timeout=5)
        deleter.start()
        assert deleting.wait(timeout=5)
        deleter.join(timeout=0.3)
        # A DELETE must not slip between recording a connected registration
        # and its POST: it would leave a recreated but unregistered bucket.
        assert deleter.is_alive()
    finally:
        release_post.set()
        creator.join(timeout=5)
        if deleter.ident is not None:
            deleter.join(timeout=5)
    assert not creator.is_alive() and not deleter.is_alive()
    assert not errors
    assert operations == [("create", "buckets/shared"), ("delete", "buckets/shared")]
    assert client.request_queue._registered_buckets == []
    # A later reconnect must respect the final delete, not recreate the bucket.
    operations.clear()
    assert client.request_queue._try_connect() is True
    assert operations == []
