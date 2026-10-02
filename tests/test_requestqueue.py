"""
Tests the often-buggy request queue.

WARNING: A shitload of mocking ahead

It is said about testing that it makes you able to refactorize
with confidence, and I need some of that right now.
"""

from time import sleep
from logging import basicConfig, DEBUG

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


def _http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(response=response)


@pytest.mark.parametrize(
    "error",
    [
        # e.g. a lone surrogate rejected by the server (activitywatch#815)
        _http_error(400),
        _http_error(404),
        _http_error(413),
        _http_error(422),
        TypeError("payload is not serializable"),
    ],
)
def test_permanently_rejected_request_does_not_block_queue(tmp_path, error):
    """A request the server permanently rejects must be dropped, not retried
    forever: the requests queued behind it still have to be sent."""
    sent = []

    class RejectingClient(MockClient):
        def _post(self, endpoint, data, *args, **kwargs):
            if data["poison"]:
                raise error
            sent.append(data)
            return requests.Response()

    client = RejectingClient()
    rq = RequestQueue(client, str(tmp_path / "queue"))  # type: ignore
    rq.add_request("/api/0/buckets/test/heartbeat", {"poison": True})
    rq.add_request("/api/0/buckets/test/heartbeat", {"poison": False})

    rq._dispatch_request()  # poisoned request: dropped
    rq._dispatch_request()  # next request: must still go out

    assert sent == [{"poison": False}]
    assert rq._persistqueue.qsize() == 0
