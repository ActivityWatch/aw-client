import json
import logging
import socket
from typing import Optional, List

import requests

from aw_core.models import Event
from . import config

# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)


# TODO: Implement auth
class ActivityWatchClient:
    def __init__(self, client_name: str, testing=False):
        self.logger = logging.getLogger("aw.client")
        self.testing = testing

        self.session = {}

        self.client_name = client_name + ("-testing" if testing else "")
        self.client_hostname = socket.gethostname()

        self.server_hostname = config["server_hostname"] if not testing else config["testserver_hostname"]
        self.server_port = config["server_port"] if not testing else config["testserver_port"]

    def _post(self, endpoint: str, data: dict) -> Optional[requests.Response]:
        headers = {"Content-type": "application/json"}
        url = "http://{}:{}/api/0/{}".format(self.server_hostname, self.server_port, endpoint)
        response = requests.post(url, data=json.dumps(data), headers=headers)
        response.raise_for_status()
        return response

    def _get(self, endpoint: str) -> Optional[requests.Response]:
        url = "http://{}:{}/api/0/{}".format(self.server_hostname, self.server_port, endpoint)
        response = requests.get(url)
        response.raise_for_status()
        return response

    def send_event(self, bucket, event: Event):
        endpoint = "buckets/{}/events".format(bucket)
        data = event.to_json_dict()
        self._post(endpoint, data)
        self.logger.debug("Sent event to server: {}".format(event))

    def send_events(self, bucket, events: List[Event]):
        endpoint = "buckets/{}/events".format(bucket)
        data = [event.to_json_dict() for event in events]
        self._post(endpoint, data)
        self.logger.debug("Sent events to server: {}".format(events))

    def replace_last_event(self, bucket, event: Event):
        endpoint = "buckets/{}/events/replace_last".format(bucket)
        data = event.to_json_dict()
        self._post(endpoint, data)
        self.logger.debug("Sent event to server: {}".format(event))

    def get_buckets(self):
        return self._get('buckets').json()

    def get_events(self, bucket) -> List[Event]:
        endpoint = "buckets/{}/events".format(bucket)
        events = self._get(endpoint).json()
        return [Event(**event) for event in events]

    def create_bucket(self, bucket_id, event_type: str) -> bool:
        # Check if bucket exists
        buckets = self.get_buckets()
        if bucket_id in buckets:
            # Don't do anything if bucket already exists
            return False
        else:
            # Create bucket
            endpoint = "buckets/{}".format(bucket_id)
            data = {
                'client': self.client_name,
                'hostname': self.client_hostname,
                'type': event_type,
            }
            self._post(endpoint, data)
            return True
