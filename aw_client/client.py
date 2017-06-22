import json
import logging
import socket
import os
import time
import threading
from datetime import datetime
from collections import namedtuple
from typing import Optional, List, Any
from queue import Queue

import requests as req

from aw_core.models import Event
from aw_core.dirs import get_data_dir

from .config import load_config
from .singleinstance import SingleInstance


# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class ActivityWatchClient:
    def __init__(self, client_name: str, testing=False) -> None:
        self.testing = testing

        self.buckets = []  # type: List[Dict[str, str]]
        # self.session = {}

        self.client_name = client_name + ("-testing" if testing else "")
        self.client_hostname = socket.gethostname()

        self.instance = SingleInstance(self.client_name)

        client_config = load_config()
        configsection = "server" if not testing else "server-testing"

        self.server_hostname = client_config[configsection]["hostname"]
        self.server_port = client_config[configsection]["port"]

        self.dispatch_thread = PostDispatchThread(self)

    #
    #   Get/Post base requests
    #

    def _url(self, endpoint: str):
        return "http://{}:{}/api/0/{}".format(self.server_hostname, self.server_port, endpoint)

    def _get(self, endpoint: str, params=None) -> Optional[req.Response]:
        response = req.get(self._url(endpoint), params=params)
        response.raise_for_status()
        return response

    def _post(self, endpoint: str, data: Any) -> Optional[req.Response]:
        headers = {"Content-type": "application/json"}
        response = req.post(self._url(endpoint), data=json.dumps(data), headers=headers)
        response.raise_for_status()
        return response

    def _delete(self, endpoint: str, data: Any = {}) -> Optional[req.Response]:
        headers = {"Content-type": "application/json"}
        response = req.delete(self._url(endpoint), data=json.dumps(data), headers=headers)
        response.raise_for_status()
        return response

    def get_info(self):
        """Returns a dict currently containing the keys 'hostname' and 'testing'."""
        endpoint = "info/"
        return self._get(endpoint).json()

    #
    #   Event get/post requests
    #

    def get_events(self, bucket: str, limit: int=None, start: datetime=None, end: datetime=None) -> List[Event]:
        endpoint = "buckets/{}/events".format(bucket)

        params = dict()  # type: Dict[str, str]
        if limit:
            params["limit"] = str(limit)
        if start:
            params["start"] = start.isoformat()
        if end:
            params["end"] = end.isoformat()

        events = self._get(endpoint, params=params).json()
        return [Event(**event) for event in events]

    def send_event(self, bucket: str, event: Event):
        endpoint = "buckets/{}/events".format(bucket)
        data = event.to_json_dict()
        return self._post(endpoint, data)

    def send_events(self, bucket: str, events: List[Event]):
        endpoint = "buckets/{}/events".format(bucket)
        data = [event.to_json_dict() for event in events]
        return self._post(endpoint, data)

    def heartbeat(self, bucket, event: Event, pulsetime: float, queued=False) -> Optional[Event]:
        """ This endpoint can use the failed requests retry queue.
            This makes the request itself non-blocking and therefore
            the function will in that case always returns None. """

        endpoint = "buckets/{}/heartbeat?pulsetime={}".format(bucket, pulsetime)
        data = event.to_json_dict()
        if queued:
            self.dispatch_thread.add_request(endpoint, data)
        else:
            return Event(**self._post(endpoint, data).json())

    #
    #   Bucket get/post requests
    #

    def get_buckets(self):
        return self._get('buckets/').json()

    def create_bucket(self, bucket_id: str, event_type: str):
        endpoint = "buckets/{}".format(bucket_id)
        data = {
            'client': self.client_name,
            'hostname': self.client_hostname,
            'type': event_type,
        }
        self._post(endpoint, data)

    def delete_bucket(self, bucket_id: str):
        self._delete('buckets/{}'.format(bucket_id))

    def setup_bucket(self, bucket_id: str, event_type: str):
        self.buckets.append({"bid": bucket_id, "etype": event_type})

    def _create_buckets(self):
        # Check if bucket exists
        buckets = self.get_buckets()
        for bucket in self.buckets:
            if bucket['bid'] in buckets:
                return False  # Don't do anything if bucket already exists
            else:
                self.create_bucket(bucket['bid'], bucket['etype'])
                return True

    #
    #   Connection methods
    #

    def connect(self):
        if not self.dispatch_thread.is_alive():
            self.dispatch_thread.start()

    def disconnect(self):
        # FIXME: doesn't disconnect immediately
        self.dispatch_thread.running = False


QueuedRequest = namedtuple("QueuedRequest", ["endpoint", "data"])


class PostDispatchThread(threading.Thread):
    def __init__(self, client, dispatch_interval=0):
        threading.Thread.__init__(self, daemon=True)
        self.running = True
        self.connected = False

        # Time to wait between dispatching events, useful for throttling.
        self.dispatch_interval = dispatch_interval

        self.client = client
        self._queue = Queue()

        # Setup failed queues file
        data_dir = get_data_dir("aw-client")
        failed_queues_dir = os.path.join(data_dir, "failed_requests")
        if not os.path.exists(failed_queues_dir):
            os.makedirs(failed_queues_dir)
        self.queue_file = os.path.join(failed_queues_dir, self.client.client_name)

    def _queue_to_file(self, endpoint: str, data: dict):
        entry = QueuedRequest(endpoint=endpoint, data=data)
        with open(self.queue_file, "a+") as queue_fp:
            queue_fp.write(json.dumps(entry) + "\n")

    def _load_queue(self):
        # If crash when lost connection, queue failed requests
        failed_requests = []  # type: List[QueuedRequests]

        # Load failed events from queue into failed_requests
        open(self.queue_file, "a").close()  # Create file if doesn't exist
        with open(self.queue_file, "r") as queue_fp:
            for request in queue_fp:
                logger.debug(request)
                try:
                    failed_requests.append(QueuedRequest(*json.loads(request)))
                except json.decoder.JSONDecodeError as e:
                    logger.error(e, exc_info=True)
                    logger.error("Request that failed: {}".format(request))
                    logger.warning("Skipping request that failed to load")

        # Insert failed_requests into dispatching queue
        # FIXME: We really shouldn't be clearing the file here until the events have been sent to server.
        open(self.queue_file, "w").close()  # Clear file
        if len(failed_requests) > 0:
            for request in failed_requests:
                self._queue.put(request)
            logger.info("Loaded {} failed requests from queuefile".format(len(failed_requests)))

    def _save_queue(self):
        # When lost connection, save queue to file for later sending
        with open(self.queue_file, "w") as queue_fp:
            while not self._queue.empty():
                # The `block=False` and `if request is not None` stuff here is actually required, see Python docs.
                request = self._queue.get(block=False)
                if request is not None:
                    queue_fp.write(json.dumps(request) + "\n")

    def _try_connect(self) -> bool:
        try:  # Try to connect
            self.client._create_buckets()
            return True
        except req.RequestException:
            return False

    # TODO: Handle SIGTERM/keyboard interrupt gracefully by saving to file first
    # FIXME: Turns out this is a really bad idea, it's probably better if the
    # entire program crashes should this thread crash. That way we can at least
    # detect errors by detecting crashes in aw-qt. I just lost a day of data due
    # to this, caused by "no space left on device" that corrupted the file.
    # Bad state should be handled with precision, not with shit like this.
    # @restart_on_exception
    def run(self):
        while self.running:
            # Connect
            while not self.connected and self.running:
                self.connected = self._try_connect()
                if self.connected:
                    logger.info("Connection to aw-server established")
                else:
                    time.sleep(10)

            # Load requests from queuefile
            self._load_queue()

            # Dispatch requests to server
            while self.connected and self.running:
                time.sleep(self.dispatch_interval)
                request = self._queue.get()
                try:
                    self.client._post(request.endpoint, request.data)
                except req.RequestException as e:
                    self._queue.queue.appendleft(request)
                    self.connected = False
                    logger.warning("Failed to send request to aw-server, will queue requests until connection is available.")
                    logger.warning(e)
                    time.sleep(1)

            # Disconnected or self.running set to false, save remaining to queuefile
            self._save_queue()

    def add_request(self, endpoint, data):
        if self.connected:
            self._queue.put(QueuedRequest(endpoint=endpoint, data=data))
        else:
            self._queue_to_file(endpoint, data)
