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
    rq = RequestQueue(client)

    # Mockeypatching
    rq._try_connect = lambda: True
    rq.connected = True

    rq.start()
    rq.add_request("/api/0/buckets/test/heartbeat", {})
    sleep(1)
    rq.stop()
    rq.join()


def test_complex():
    client = MockClient()
    rq = RequestQueue(client)

    # Mockeypatching
    rq._try_connect = lambda: False

    rq.start()
    sleep(1)
    rq.stop()
    rq.join()
