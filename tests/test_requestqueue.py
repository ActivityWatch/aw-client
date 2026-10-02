"""
Tests the often-buggy request queue.

WARNING: A shitload of mocking ahead

It is said about testing that it makes you able to refactorize
with confidence, and I need some of that right now.
"""

import time
from datetime import datetime, timedelta, timezone
from time import sleep
from logging import basicConfig, DEBUG

basicConfig(level=DEBUG)

import pytest
import requests

from aw_client.client import RequestQueue

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _heartbeat(offset_s, duration_s=10.0, data=None):
    return {
        "timestamp": (_BASE + timedelta(seconds=offset_s)).isoformat(),
        "duration": duration_s,
        "data": {"status": "not-afk"} if data is None else data,
    }


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


class RecordingClient(MockClient):
    """Client that records every (endpoint, data) posted to it."""

    def __init__(self):
        super().__init__()
        self.posts = []
        self.post_calls = 0

    def _post(self, endpoint, data, **kwargs):
        self.posts.append((endpoint, data))
        self.post_calls += 1
        return requests.Response()


def test_dispatch_merges_consecutive_queued_heartbeats():
    """
    A long offline backlog of heartbeats for one bucket must collapse into a
    handful of requests instead of one per heartbeat, without losing data
    (issue #32 / #7).
    """
    client = RecordingClient()
    rq = _fresh_queue(client)
    rq.connected = True

    n = 200
    for i in range(n):
        rq.add_request("buckets/test/heartbeat?pulsetime=10", _heartbeat(i * 10))

    rq._dispatch_request()

    assert client.post_calls == 1, (
        "mergeable heartbeats should collapse into one request"
    )
    assert rq._get_next() is None, "the queue should be fully drained"
    endpoint, data = client.posts[0]
    assert endpoint == "buckets/test/heartbeat?pulsetime=10"
    # No data loss: the merged event spans the whole queued range.
    assert data["duration"] == pytest.approx(n * 10)
    assert data["data"] == {"status": "not-afk"}


def test_dispatch_does_not_merge_heartbeats_with_different_data():
    """Heartbeats whose data differs must each be sent, in order."""
    client = RecordingClient()
    rq = _fresh_queue(client)
    rq.connected = True

    for i in range(5):
        rq.add_request(
            "buckets/test/heartbeat?pulsetime=10",
            _heartbeat(i * 10, data={"title": f"window-{i}"}),
        )

    rq._dispatch_request()

    assert client.post_calls == 5
    assert [d["data"]["title"] for _, d in client.posts] == [
        f"window-{i}" for i in range(5)
    ]
    assert rq._get_next() is None


def test_dispatch_batches_across_buckets():
    """Interleaved buckets are grouped, so each bucket drains in one request."""
    client = RecordingClient()
    rq = _fresh_queue(client)
    rq.connected = True

    for i in range(50):
        rq.add_request("buckets/afk/heartbeat?pulsetime=10", _heartbeat(i * 10))
        rq.add_request(
            "buckets/window/heartbeat?pulsetime=10",
            _heartbeat(i * 10, data={"title": "same"}),
        )

    rq._dispatch_request()

    assert client.post_calls == 2
    assert sorted(endpoint for endpoint, _ in client.posts) == [
        "buckets/afk/heartbeat?pulsetime=10",
        "buckets/window/heartbeat?pulsetime=10",
    ]
    assert rq._get_next() is None


def test_dispatch_retry_keeps_whole_batch_then_merges():
    """A transient error retains the whole batch; the retry merges it."""
    client = FlakyClient(_http_error(503))
    rq = _fresh_queue(client)
    rq.connected = True

    n = 50
    for i in range(n):
        rq.add_request("buckets/test/heartbeat?pulsetime=10", _heartbeat(i * 10))

    rq._dispatch_request()

    assert client.post_calls == 1
    assert rq._get_next() is not None  # batch retained

    client.exc = None  # server recovered
    rq._dispatch_request()

    assert client.post_calls == 2  # one merged request on retry
    assert rq._get_next() is None


def test_dispatch_drops_bad_request_but_keeps_the_rest():
    """
    A non-retryable error (HTTP 400) drops only that request; the rest of the
    batch must still be dispatched and the queue fully drained.
    """

    class SelectiveFailClient(RecordingClient):
        def _post(self, endpoint, data, **kwargs):
            self.post_calls += 1
            if "bad" in endpoint:
                raise _http_error(400)
            self.posts.append((endpoint, data))
            return requests.Response()

    client = SelectiveFailClient()
    rq = _fresh_queue(client)
    rq.connected = True

    rq.add_request("buckets/bad/heartbeat?pulsetime=10", _heartbeat(0))
    rq.add_request("buckets/good/heartbeat?pulsetime=10", _heartbeat(0, data={"k": 1}))

    rq._dispatch_request()

    assert client.post_calls == 2
    assert [endpoint for endpoint, _ in client.posts] == [
        "buckets/good/heartbeat?pulsetime=10"
    ]
    assert rq._get_next() is None


def test_dispatch_drains_10k_heartbeats_in_batches():
    """
    A large offline backlog (10k mergeable heartbeats) must drain with one
    request per batch, quickly and without losing data.
    """
    client = RecordingClient()
    rq = _fresh_queue(client)
    rq.connected = True

    n = 10_000
    for i in range(n):
        rq.add_request("buckets/test/heartbeat?pulsetime=10", _heartbeat(i * 10))

    start = time.monotonic()
    while rq._get_next_batch():
        rq._dispatch_request()
    elapsed = time.monotonic() - start

    # Every batch merges to a single heartbeat request.
    assert client.post_calls == n // rq.BATCH_SIZE
    assert rq._get_next() is None
    # The first request already spans the whole backlog (no data loss).
    assert client.posts[0][1]["duration"] > 0
    assert elapsed < 10, f"draining 10k heartbeats took {elapsed:.1f}s"
