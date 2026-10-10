"""
Tests the often-buggy request queue.

WARNING: A shitload of mocking ahead

It is said about testing that it makes you able to refactorize
with confidence, and I need some of that right now.
"""

from time import sleep
from logging import basicConfig, DEBUG, WARNING

basicConfig(level=DEBUG)

import requests
from aw_client.client import RequestQueue


class MockClient:
    client_name = "Mock"

    def __init__(self):
        self.testing = True
        self.create_bucket_calls = []

    def get_buckets(self, *args, **kwargs):
        print("Called get_buckets")
        return [{"id": "test", "name": "Test"}]

    def create_bucket(self, *args, **kwargs):
        self.create_bucket_calls.append((args, kwargs))
        print("Called create_bucket")

    def _post(self, *args, **kwargs):
        print(args, kwargs)
        return requests.Response()


def test_basic():
    client = MockClient()
    rq = RequestQueue(client)  # type: ignore

    # Mockeypatching
    rq._try_connect = lambda: True  # type: ignore
    rq.connected = True

    rq.start()
    rq.add_request("/api/0/buckets/test/heartbeat", {})
    sleep(1)
    rq.stop()
    rq.join()


def test_complex():
    client = MockClient()
    rq = RequestQueue(client)  # type: ignore

    # Mockeypatching
    rq._try_connect = lambda: False  # type: ignore

    rq.start()
    sleep(1)
    rq.stop()
    rq.join()


def test_register_bucket_creates_immediately_when_connected():
    client = MockClient()
    rq = RequestQueue(client)  # type: ignore
    rq.connected = True

    rq.register_bucket("test-bucket", "test-type")

    assert client.create_bucket_calls == [(("test-bucket", "test-type"), {})]


def test_register_bucket_marks_queue_disconnected_on_create_failure():
    class FailingClient(MockClient):
        def create_bucket(self, *args, **kwargs):
            super().create_bucket(*args, **kwargs)
            raise requests.exceptions.ConnectionError()

    client = FailingClient()
    rq = RequestQueue(client)  # type: ignore
    rq.connected = True

    rq.register_bucket("test-bucket", "test-type")

    assert rq.connected is False
    assert client.create_bucket_calls == [(("test-bucket", "test-type"), {})]


def test_add_request_disk_full(caplog):
    """Ensures that add_request doesn't crash if the queue can't be written to disk"""
    client = MockClient()
    rq = RequestQueue(client)  # type: ignore

    def raise_oserror(*args, **kwargs):
        raise OSError("No space left on device")

    rq._persistqueue.put = raise_oserror  # type: ignore

    # Should not raise, the OSError should be caught internally and logged instead
    with caplog.at_level(WARNING, logger="aw_client.client"):
        assert rq.add_request("/api/0/buckets/test/heartbeat", {}) is False
        # A sustained failure warns once, not once per heartbeat
        assert rq.add_request("/api/0/buckets/test/heartbeat", {}) is False

    warnings = [r for r in caplog.records if "Failed to queue request" in r.message]
    assert len(warnings) == 1


def test_add_request_sqlite_full_then_recovers(tmp_path):
    """A real full SQLite database raises OperationalError, not OSError."""
    client = MockClient()
    rq = RequestQueue(client, persistqueue_path=str(tmp_path / "q"))  # type: ignore
    putter = rq._persistqueue._putter
    putter.execute("PRAGMA max_page_count=3")  # type: ignore

    data = {"data": "x" * 500}
    results = [rq.add_request("/api/0/buckets/test/heartbeat", data) for _ in range(20)]
    assert results[0] is True
    assert results[-1] is False

    # Once space is available again, writes succeed
    putter.execute("PRAGMA max_page_count=1073741823")  # type: ignore
    assert rq.add_request("/api/0/buckets/test/heartbeat", data) is True
