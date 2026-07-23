"""
Tests the often-buggy request queue.

WARNING: A shitload of mocking ahead

It is said about testing that it makes you able to refactorize
with confidence, and I need some of that right now.
"""

import threading
from time import sleep
from logging import basicConfig, DEBUG, WARNING

basicConfig(level=DEBUG)

import pytest
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


def test_wait_for_queue_empty_basic():
    """Queue empties normally while connected and running."""
    client = MockClient()
    rq = RequestQueue(client)  # type: ignore
    rq.start()

    rq.add_request("/api/0/buckets/test/heartbeat", {})
    result = rq.wait_for_queue_empty(timeout=5)

    rq.stop()
    rq.join()
    assert result is True


def test_wait_for_queue_empty_not_running():
    """Returns True immediately if the queue thread isn't running."""
    client = MockClient()
    rq = RequestQueue(client)  # type: ignore
    # Thread never started, should return True instantly
    result = rq.wait_for_queue_empty(timeout=5)
    assert result is True


def test_wait_for_queue_empty_pending_not_running():
    """Returns False when requests are pending but the thread isn't running."""
    client = MockClient()
    rq = RequestQueue(client)  # type: ignore
    # Nothing can flush this request: the thread was never started.
    rq.add_request("/api/0/buckets/test/heartbeat", {})
    result = rq.wait_for_queue_empty(timeout=5)
    assert result is False


def test_wait_for_queue_empty_timeout():
    """Returns False if the queue doesn't empty before the timeout."""
    import unittest.mock as mock

    client = MockClient()
    rq = RequestQueue(client)  # type: ignore

    # Block _post until the test releases it, so the queue can't empty before
    # the timeout without adding a multi-second sleep to every run.
    release = threading.Event()

    def slow_post(endpoint, data):
        release.wait(10)

    with mock.patch.object(client, "_post", slow_post):
        rq.start()
        rq.add_request("/api/0/buckets/test/heartbeat", {})
        try:
            result = rq.wait_for_queue_empty(timeout=0.5)
        finally:
            release.set()
            rq.stop()
            rq.join()

    assert result is False


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


def _http_error(status_code: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(response=response)


class FlakyClient(MockClient):
    """Client whose _post raises the given exception until cleared."""

    def __init__(self, exc):
        super().__init__()
        self.exc = exc
        self.post_calls = 0

    def _post(self, *args, **kwargs):
        self.post_calls += 1
        if self.exc:
            raise self.exc
        return requests.Response()


def _fresh_queue(client) -> RequestQueue:
    """Create a RequestQueue and drain requests persisted by earlier runs."""
    rq = RequestQueue(client)  # type: ignore
    while rq._get_next():
        rq._task_done()
    return rq


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_dispatch_retries_transient_server_errors(status_code):
    """
    Transient server-side errors (e.g. 503 from aw-server's heartbeat-lock
    timeout) must keep the request in the queue for a later retry, then
    dispatch it once the server recovers.

    Also guards against the Response.__bool__ pitfall: `if e.response` is
    False for any error status, which used to send every HTTP error down the
    "not retrying" path (dropping the request permanently).
    """
    client = FlakyClient(_http_error(status_code))
    rq = _fresh_queue(client)
    rq.connected = True

    rq.add_request("buckets/test/heartbeat?pulsetime=10", {"label": "test"})
    rq._dispatch_request()

    assert client.post_calls == 1
    assert rq._get_next() is not None  # still queued

    client.exc = None  # server recovered
    rq._dispatch_request()

    assert client.post_calls == 2
    assert rq._get_next() is None  # delivered and popped


def test_dispatch_drops_client_errors():
    """A bad payload (HTTP 400) fails forever and must not block the queue."""
    client = FlakyClient(_http_error(400))
    rq = _fresh_queue(client)
    rq.connected = True

    rq.add_request("buckets/test/heartbeat?pulsetime=10", {"label": "bad"})
    rq._dispatch_request()

    assert client.post_calls == 1
    assert rq._get_next() is None  # dropped


def test_dispatch_keeps_queue_on_connection_error():
    """
    A connection error mid-dispatch (server died after connect) must keep the
    request queued and mark the queue disconnected, so the run loop goes back
    to reconnecting instead of draining the queue into the void.
    """
    client = FlakyClient(requests.exceptions.ConnectionError())
    rq = _fresh_queue(client)
    rq.connected = True

    rq.add_request("buckets/test/heartbeat?pulsetime=10", {"label": "test"})
    rq._dispatch_request()

    assert rq._get_next() is not None  # still queued
    assert rq.connected is False


def test_retry_delay_honors_retry_after():
    from aw_client.client import _retry_delay, RETRY_DELAY_DEFAULT, RETRY_DELAY_MAX

    def resp(retry_after=None):
        r = requests.Response()
        r.status_code = 429
        if retry_after is not None:
            r.headers["Retry-After"] = retry_after
        return r

    assert _retry_delay(resp()) == RETRY_DELAY_DEFAULT  # absent
    assert _retry_delay(resp("2")) == 2.0  # delta-seconds
    assert _retry_delay(resp("9999")) == RETRY_DELAY_MAX  # capped
    assert _retry_delay(resp("0")) == RETRY_DELAY_DEFAULT  # floored
    # HTTP-date form is not parsed, falls back to default
    assert _retry_delay(resp("Wed, 21 Oct 2026 07:28:00 GMT")) == RETRY_DELAY_DEFAULT
