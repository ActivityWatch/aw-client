import json
import logging
import socket
import os
import time
import threading
from functools import wraps
from typing import Optional, List, Union, Callable
from collections import namedtuple, deque

import requests

from aw_core.models import Event
from . import config

# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)

AWRequest = namedtuple("AWRequest", ["action", "args", "kwargs"])


# TODO: Needs authentication
class ActivityWatchClient:
    def __init__(self, client_name: str, testing=False, use_queue=True):
        self.logger = logging.getLogger("aw.client")
        self.testing = testing

        self.session = {}

        self.client_name = client_name + ("-testing" if testing else "")
        self.client_hostname = socket.gethostname()

        self.server_hostname = config["server_hostname"] if not testing else config["testserver_hostname"]
        self.server_port = config["server_port"] if not testing else config["testserver_port"]

        # Send old failed events
        self._post_failed_events()

        self.dispatcher = RequestDispatcher(self)
        self.dispatcher.start()

    def _send_request(self, req: AWRequest):
        """Should only be called by the dispatcher"""
        f = getattr(self, req.action)
        f(*req.args, **req.kwargs, is_queued=True)

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

    def queue_request(self, f) -> Callable[..., bool]:
        """Decorator that queues requests using RequestDispatcher if self.use_queue is True"""
        # FIXME: args and kwargs might contain unserializable data
        if self.use_queue:
            @wraps(f)
            def g(self, *args, is_queued=False, **kwargs) -> bool:
                if is_queued:
                    # Call was coming from dispatcher queue
                    req = {"action": f.__name__, "args": args, "kwargs": kwargs}
                    try:
                        self._send_request(req)
                        return True
                    except req.RequestException as e:
                        self.logger.warning("Failed to send request to server (req: {}, e: {})".format(req, e))
                        return False
                else:
                    # Call was coming from outside of aw-client
                    # Send a request to the request queue
                    self.dispatcher.queue(AWRequest(f.__name__, args, kwargs))
            return g
        else:
            return f

    @queue_request
    def send_event(self, bucket: str, event: Union[dict, Event]):
        endpoint = "buckets/{}/events".format(bucket)
        if isinstance(event, Event):
            event = event.to_json_dict()
        self._post(endpoint, event)
        self.logger.debug("Sent event to server: {}".format(event))

    @queue_request
    def send_events(self, bucket: str, events: Union[List[dict], List[Event]]):
        endpoint = "buckets/{}/events".format(bucket)
        if isinstance(events[0], Event):
            events = [event.to_json_dict() for event in events]
        self._post(endpoint, events)
        self.logger.debug("Sent events to server: {}".format(events))

    @queue_request
    def replace_last_event(self, bucket: str, event: Event):
        endpoint = "buckets/{}/events/replace_last".format(bucket)
        data = event.to_json_dict()
        self._post(endpoint, data)
        self.logger.debug("Sent event to server: {}".format(event))

    def get_buckets(self):
        return self._get('buckets').json()

    @queue_request
    def create_bucket(self, bucket_id: str, event_type: str):
        # Check if bucket exists
        buckets = self.get_buckets()
        if bucket_id in buckets:
            return False  # Don't do anything if bucket already exists
        else:
            # Create bucket
            endpoint = "buckets/{}".format(bucket_id)
            data = {
                'client': self.client_name,
                'hostname': self.client_hostname,
                'type': event_type,
            }
            self._post(endpoint, data)


class RequestDispatcher(threading.Thread):
    """
    Maintains a queue of requests to be sent,
    resolving their futures when they are done
    and retrying them if they fail.
    """

    def __init__(self, client):
        threading.Thread.__init__(self)
        self.daemon = True
        self.client = client
        self._queue = deque()

        # Setup failed requests queue file
        queue_dir = os.path.join(client.data_dir, "failed_requests")
        self.queue_file = os.path.join(queue_dir, client.client_name + ".json")
        if not os.path.exists(self.queue_dir):
            os.makedirs(self.queue_dir)

    def run(self):
        while True:
            # TODO: Use Event.wait here instead, such that if queue
            # is empty and an request arrives, it triggers the event
            # and is dispatched immediately.
            time.sleep(180)

            self._save_queue()
            self._dispatch()
            self._save_queue()

    def queue(self, req: AWRequest):
        self._queue.append(req)

    def _save_queue(self):
        lines = [json.dumps(req) for req in self._queue]
        with open(self.queue_file, "w") as fp:
            fp.writelines(lines)

    def _dispatch(self):
        while len(self._queue) != 0:
            request = self._queue[0]
            # TODO: Check if successful
            success = self.client._send_request()

            if success:
                # If successful
                self.logger.info("Sent request: {}".format(request))
                self._queue.popleft()
            else:
                # If unsuccessful, stop trying to send requests
                break
