"""
Tests the often-buggy request queue.

WARNING: A shitload of mocking ahead

It is said about testing that it makes you able to refactorize
with confidence, and I need some of that right now.
"""

import threading
from datetime import datetime, timedelta, timezone
from time import sleep
from logging import basicConfig, DEBUG, WARNING

basicConfig(level=DEBUG)

import pytest
import requests

from aw_core.models import Event

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


def _fresh_queue(client, tmp_path) -> RequestQueue:
    """Create a RequestQueue backed by an isolated, empty on-disk queue."""
    return RequestQueue(client, persistqueue_path=str(tmp_path / "queue"))  # type: ignore


def _drain(rq) -> None:
    """Dispatch until the queue is empty (only for clients that succeed)."""
    while rq._get_next_batch():
        rq._dispatch_request()


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_dispatch_retries_transient_server_errors(status_code, tmp_path):
    """
    Transient server-side errors (e.g. 503 from aw-server's heartbeat-lock
    timeout) must keep the request in the queue for a later retry, then
    dispatch it once the server recovers.

    Also guards against the Response.__bool__ pitfall: `if e.response` is
    False for any error status, which used to send every HTTP error down the
    "not retrying" path (dropping the request permanently).
    """
    client = FlakyClient(_http_error(status_code))
    rq = _fresh_queue(client, tmp_path)
    rq.connected = True

    rq.add_request("buckets/test/heartbeat?pulsetime=10", {"label": "test"})
    rq._dispatch_request()

    assert client.post_calls == 1
    assert rq._get_next() is not None  # still queued

    client.exc = None  # server recovered
    rq._dispatch_request()

    assert client.post_calls == 2
    assert rq._get_next() is None  # delivered and popped


def test_dispatch_drops_client_errors(tmp_path):
    """A bad payload (HTTP 400) fails forever and must not block the queue."""
    client = FlakyClient(_http_error(400))
    rq = _fresh_queue(client, tmp_path)
    rq.connected = True

    rq.add_request("buckets/test/heartbeat?pulsetime=10", {"label": "bad"})
    rq._dispatch_request()

    assert client.post_calls == 1
    assert rq._get_next() is None  # dropped


def test_dispatch_keeps_queue_on_connection_error(tmp_path):
    """
    A connection error mid-dispatch (server died after connect) must keep the
    request queued and mark the queue disconnected, so the run loop goes back
    to reconnecting instead of draining the queue into the void.
    """
    client = FlakyClient(requests.exceptions.ConnectionError())
    rq = _fresh_queue(client, tmp_path)
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

    def get_events(self, bucket_id, limit=-1, start=None, end=None):
        return []

    def sent_events(self):
        """Every event posted, in order, whether by heartbeat or bulk insert."""
        out = []
        for _, data in self.posts:
            out.extend(data if isinstance(data, list) else [data])
        return out


class StoringClient(RecordingClient):
    """Fake server: stores inserted events and serves them from get_events.

    `fail_insert_after_store` makes the next bulk insert store its events and
    then raise, i.e. the request succeeded but the response was lost.
    """

    def __init__(self):
        super().__init__()
        self.stored = []
        self.fail_insert_after_store = None

    def _post(self, endpoint, data, **kwargs):
        if endpoint.endswith("/events"):
            self.stored.extend(Event(**d) for d in data)
            if self.fail_insert_after_store is not None:
                exc, self.fail_insert_after_store = self.fail_insert_after_store, None
                raise exc
        else:
            self.stored.append(Event(**data))
        return super()._post(endpoint, data, **kwargs)

    def get_events(self, bucket_id, limit=-1, start=None, end=None):
        return [
            e
            for e in self.stored
            if e.timestamp < end and e.timestamp + e.duration > start
        ]


def test_dispatch_merges_consecutive_queued_heartbeats(tmp_path):
    """
    A long offline backlog of heartbeats for one bucket must collapse into a
    handful of requests instead of one per heartbeat, without losing data
    (issue #32 / #7).
    """
    client = RecordingClient()
    rq = _fresh_queue(client, tmp_path)
    rq.connected = True

    n = 200
    for i in range(n):
        rq.add_request("buckets/test/heartbeat?pulsetime=10", _heartbeat(i * 10))

    rq._dispatch_request()

    assert (
        client.post_calls == 1
    ), "mergeable heartbeats should collapse into one request"
    assert rq._get_next() is None, "the queue should be fully drained"
    endpoint, data = client.posts[0]
    assert endpoint == "buckets/test/heartbeat?pulsetime=10"
    # No data loss: the merged event spans the whole queued range.
    assert data["duration"] == pytest.approx(n * 10)
    assert data["data"] == {"status": "not-afk"}


def test_dispatch_does_not_merge_heartbeats_with_different_data(tmp_path):
    """Heartbeats whose data differs must each be sent, in order."""
    client = RecordingClient()
    rq = _fresh_queue(client, tmp_path)
    rq.connected = True

    for i in range(5):
        rq.add_request(
            "buckets/test/heartbeat?pulsetime=10",
            _heartbeat(i * 10, data={"title": f"window-{i}"}),
        )

    _drain(rq)

    # First and last as heartbeats, the middle three in one bulk insert.
    assert [endpoint for endpoint, _ in client.posts] == [
        "buckets/test/heartbeat?pulsetime=10",
        "buckets/test/events",
        "buckets/test/heartbeat?pulsetime=10",
    ]
    assert [d["data"]["title"] for d in client.sent_events()] == [
        f"window-{i}" for i in range(5)
    ]
    assert rq._get_next() is None


def test_dispatch_bulk_inserts_unmergeable_backlog_in_chunks():
    """
    A backlog of events that cannot merge (e.g. changing window titles) is
    sent as heartbeat + bulk-insert chunks + heartbeat, not one request each,
    and every event arrives exactly once and in order (issue #32).
    """
    client = RecordingClient()
    rq = _fresh_queue(client)
    rq.connected = True

    n = 250
    for i in range(n):
        rq.add_request(
            "buckets/test/heartbeat?pulsetime=10",
            _heartbeat(i * 10, data={"title": f"window-{i}"}),
        )

    _drain(rq)

    chunks = -(-(n - 2) // rq.INSERT_CHUNK_SIZE)  # ceil
    assert client.post_calls == 2 + chunks  # 5 instead of 250
    assert client.posts[0][0] == "buckets/test/heartbeat?pulsetime=10"
    assert client.posts[-1][0] == "buckets/test/heartbeat?pulsetime=10"
    assert all(endpoint == "buckets/test/events" for endpoint, _ in client.posts[1:-1])
    assert [d["data"]["title"] for d in client.sent_events()] == [
        f"window-{i}" for i in range(n)
    ]
    assert rq._get_next() is None


def test_dispatch_short_run_stays_heartbeats():
    """
    If the whole run fits within pulsetime, the last heartbeat could merge
    into a stale cached event on aw-server (Python), so no insert is used.
    """
    client = RecordingClient()
    rq = _fresh_queue(client)
    rq.connected = True

    for i in range(4):
        rq.add_request(
            "buckets/test/heartbeat?pulsetime=10",
            _heartbeat(i, duration_s=0.5, data={"title": f"window-{i}"}),
        )

    _drain(rq)

    assert client.post_calls == 4
    assert all("/heartbeat" in endpoint for endpoint, _ in client.posts)


@pytest.mark.parametrize(
    "exc",
    [requests.exceptions.Timeout(), requests.exceptions.ConnectionError()],
)
def test_insert_retry_after_lost_response_does_not_duplicate(exc):
    """
    A bulk insert is not idempotent. If it reached the server but the
    response was lost, the retry must not insert the events a second time.
    """
    client = StoringClient()
    rq = _fresh_queue(client)
    rq.connected = True

    n = 20
    for i in range(n):
        rq.add_request(
            "buckets/test/heartbeat?pulsetime=10",
            _heartbeat(i * 10, data={"title": f"window-{i}"}),
        )

    rq._dispatch_request()  # first event, heartbeat
    client.fail_insert_after_store = exc
    rq._dispatch_request()  # insert stored, response lost -> retried
    assert rq._get_next() is not None

    _drain(rq)

    titles = [e.data["title"] for e in client.stored]
    assert titles == [f"window-{i}" for i in range(n)], "each event exactly once"
    assert rq._get_next() is None


def test_insert_retry_resends_when_nothing_was_stored():
    """A failed insert that never reached the server is resent in full."""
    client = StoringClient()
    rq = _fresh_queue(client)
    rq.connected = True

    n = 20
    for i in range(n):
        rq.add_request(
            "buckets/test/heartbeat?pulsetime=10",
            _heartbeat(i * 10, data={"title": f"window-{i}"}),
        )

    rq._dispatch_request()  # first event, heartbeat
    real_post = client._post

    def refuse(endpoint, data, **kwargs):
        raise requests.exceptions.ConnectionError()

    client._post = refuse
    rq._dispatch_request()  # insert refused before reaching the server
    client._post = real_post

    _drain(rq)

    titles = [e.data["title"] for e in client.stored]
    assert titles == [f"window-{i}" for i in range(n)]
    assert rq._get_next() is None


def test_dispatch_batches_across_buckets():
    """Interleaved buckets are grouped, so each bucket drains in one request."""
    client = RecordingClient()
    rq = _fresh_queue(client, tmp_path)
    rq.connected = True

    for i in range(50):
        rq.add_request("buckets/afk/heartbeat?pulsetime=10", _heartbeat(i * 10))
        rq.add_request(
            "buckets/window/heartbeat?pulsetime=10",
            _heartbeat(i * 10, data={"title": "same"}),
        )

    _drain(rq)

    assert client.post_calls == 2
    assert sorted(endpoint for endpoint, _ in client.posts) == [
        "buckets/afk/heartbeat?pulsetime=10",
        "buckets/window/heartbeat?pulsetime=10",
    ]
    assert rq._get_next() is None


def test_dispatch_retry_keeps_whole_batch_then_merges(tmp_path):
    """A transient error retains the whole batch; the retry merges it."""
    client = FlakyClient(_http_error(503))
    rq = _fresh_queue(client, tmp_path)
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


def test_dispatch_drops_bad_request_but_keeps_the_rest(tmp_path):
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
    rq = _fresh_queue(client, tmp_path)
    rq.connected = True

    rq.add_request("buckets/bad/heartbeat?pulsetime=10", _heartbeat(0))
    rq.add_request("buckets/good/heartbeat?pulsetime=10", _heartbeat(0, data={"k": 1}))

    _drain(rq)

    assert client.post_calls == 2
    assert [endpoint for endpoint, _ in client.posts] == [
        "buckets/good/heartbeat?pulsetime=10"
    ]
    assert rq._get_next() is None


def test_dispatch_drains_10k_heartbeats_in_batches(tmp_path):
    """
    A large offline backlog (10k mergeable heartbeats) must drain with one
    request per batch, quickly and without losing data.
    """
    client = RecordingClient()
    rq = _fresh_queue(client, tmp_path)
    rq.connected = True

    n = 10_000
    for i in range(n):
        rq.add_request("buckets/test/heartbeat?pulsetime=10", _heartbeat(i * 10))

    while rq._get_next_batch():
        rq._dispatch_request()

    # Every batch merges to a single heartbeat request.
    assert client.post_calls == n // rq.BATCH_SIZE
    assert rq._get_next() is None
    # No data loss: the merged events across all batches still cover the whole
    # queued range, not just the first request.
    total = sum(data["duration"] for _, data in client.posts)
    assert total == pytest.approx(n * 10)
    assert all(data["data"] == {"status": "not-afk"} for _, data in client.posts)
    # ...and they tile it contiguously: the first starts at the first queued
    # heartbeat, each starts where the previous ended, the last ends at the end.
    expected_start = _BASE
    for _, data in client.posts:
        start = datetime.fromisoformat(str(data["timestamp"]))
        assert start == expected_start
        expected_start = start + timedelta(seconds=data["duration"])
    assert expected_start == _BASE + timedelta(seconds=n * 10)


def test_dispatch_preserves_order_when_pulsetimes_differ(tmp_path):
    """
    Heartbeats for one bucket with different pulsetimes must not be reordered
    (grouping by endpoint alone would merge/reorder across the intervening
    one and change the server-side timeline).
    """
    client = RecordingClient()
    rq = _fresh_queue(client, tmp_path)
    rq.connected = True

    rq.add_request("buckets/test/heartbeat?pulsetime=10", _heartbeat(0))
    rq.add_request("buckets/test/heartbeat?pulsetime=20", _heartbeat(10))
    rq.add_request("buckets/test/heartbeat?pulsetime=10", _heartbeat(20))

    _drain(rq)

    assert [endpoint for endpoint, _ in client.posts] == [
        "buckets/test/heartbeat?pulsetime=10",
        "buckets/test/heartbeat?pulsetime=20",
        "buckets/test/heartbeat?pulsetime=10",
    ]
    assert rq._get_next() is None


def test_dispatch_handles_malformed_pulsetime_without_crashing(tmp_path):
    """A malformed pulsetime must not raise inside coalescing: the request is
    sent verbatim and the rest of the queue still drains."""
    client = RecordingClient()
    rq = _fresh_queue(client, tmp_path)
    rq.connected = True

    rq.add_request("buckets/test/heartbeat?pulsetime=1..2", _heartbeat(0))
    rq.add_request("buckets/test/heartbeat?pulsetime=10", _heartbeat(0, data={"k": 1}))

    _drain(rq)

    assert client.post_calls == 2
    assert rq._get_next() is None


def test_partial_retry_does_not_replay_delivered_requests(tmp_path):
    """
    If an earlier request in a batch is delivered and a later one hits a
    transient error, the retry must resume at the failed request instead of
    replaying the whole batch.
    """

    class FailSecondClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def _post(self, endpoint, data, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise _http_error(503)
            return super()._post(endpoint, data, **kwargs)

    client = FailSecondClient()
    rq = _fresh_queue(client, tmp_path)
    rq.connected = True

    # Different data, so these are two separate (non-mergeable) requests.
    rq.add_request(
        "buckets/test/heartbeat?pulsetime=10", _heartbeat(0, data={"title": "a"})
    )
    rq.add_request(
        "buckets/test/heartbeat?pulsetime=10", _heartbeat(10, data={"title": "b"})
    )

    rq._dispatch_request()  # a -> delivered
    rq._dispatch_request()  # b -> 503, stays
    rq._dispatch_request()  # b -> delivered

    assert client.calls == 3, "the failed request should be retried, not the batch"
    assert client.post_calls == 2
    assert [d["data"]["title"] for _, d in client.posts] == ["a", "b"]
    assert rq._get_next() is None


def test_rejected_insert_chunk_only_drops_the_bad_event():
    """A 400 on a bulk insert must not drop the valid events in the chunk."""

    class RejectsBadEvent(StoringClient):
        def _post(self, endpoint, data, **kwargs):
            if endpoint.endswith("/events") and any(
                d["data"]["title"] == "bad" for d in data
            ):
                self.post_calls += 1
                raise _http_error(400)
            return super()._post(endpoint, data, **kwargs)

    client = RejectsBadEvent()
    rq = _fresh_queue(client)
    rq.connected = True

    titles = [f"window-{i}" for i in range(10)]
    titles[5] = "bad"
    for i, title in enumerate(titles):
        rq.add_request(
            "buckets/test/heartbeat?pulsetime=10",
            _heartbeat(i * 10, data={"title": title}),
        )

    _drain(rq)

    assert [e.data["title"] for e in client.stored] == [t for t in titles if t != "bad"]
    assert rq._get_next() is None


def test_failed_reconcile_read_still_sends_the_chunk():
    """If the pre-retry lookup is rejected, send the chunk rather than drop it."""

    class ReadDenied(StoringClient):
        def get_events(self, *args, **kwargs):
            raise _http_error(403)

    client = ReadDenied()
    rq = _fresh_queue(client)
    rq.connected = True

    n = 10
    for i in range(n):
        rq.add_request(
            "buckets/test/heartbeat?pulsetime=10",
            _heartbeat(i * 10, data={"title": f"window-{i}"}),
        )

    _drain(rq)  # first batch after startup -> reconciles -> read denied

    assert [e.data["title"] for e in client.stored] == [f"window-{i}" for i in range(n)]
    assert rq._get_next() is None
