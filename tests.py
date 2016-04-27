import unittest

from actwa.client import ActivityWatchClient

class ClientTest(unittest.TestCase):
    def setUp(self):
        self.client = ActivityWatchClient("unittest")

    def test_auth(self):
        pass

    def test_send_event(self):
        self.client.send_event({"label": "test"})
