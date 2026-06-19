"""
Tests the often-buggy request queue.

WARNING: A shitload of mocking ahead

It is said about testing that it makes you able to refactorize
with confidence, and I need some of that right now.
"""

from time import sleep
from logging import basicConfig, DEBUG

basicConfig(level=DEBUG)

import requests
from aw_client.client import RequestQueue


class MockClient:
    client_name = "Mock"

    def __init__(self):
        self.testing = True

    def get_buckets(self, *args, **kwargs):
        print("Called get_buckets")
        return [{"id": "test", "name": "Test"}]

    def create_bucket(self, *args, **kwargs):
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

def test_add_request_disk_full():
    """Ensures that add_request doesn't crash if the queue can't be written to disk"""
    client = MockClient()
    rq = RequestQueue(client)  # type: ignore

    def raise_oserror(*args, **kwargs):
        raise OSError("No space left on device")

    rq._persistqueue.put = raise_oserror  # type: ignore

    # Should not raise, the OSError should be caught internally and logged instead
    rq.add_request("/api/0/buckets/test/heartbeat", {})

def test_wait_for_queue_empty_basic():
    """Queue empties normally while connected and running."""
    client = MockClient()
    rq = RequestQueue(client) # type: ignore
    rq.start()

    rq.add_request("/api/0/buckets/test/heartbeat", {})
    result = rq.wait_for_queue_empty(timeout=5)

    rq.stop()
    rq.join()
    assert result is True


def test_wait_for_queue_empty_not_running():
    """Returns True immediately if the queue thread isn't running."""
    client = MockClient()
    rq = RequestQueue(client) # type: ignore
    # Thread never started, should return True instantly
    result = rq.wait_for_queue_empty(timeout=5)
    assert result is True


def test_wait_for_queue_empty_timeout():
    """Returns False if the queue doesn't empty before the timeout."""
    import unittest.mock as mock

    client = MockClient()
    rq = RequestQueue(client) # type: ignore

    # Make _post block long enough that the queue won't empty before timeout
    def slow_post(endpoint, data):
        from time import sleep
        sleep(10)

    with mock.patch.object(client, "_post", slow_post):
        rq.start()
        rq.add_request("/api/0/buckets/test/heartbeat", {})
        result = rq.wait_for_queue_empty(timeout=0.5)
        rq.stop()
        rq.join()

    assert result is False