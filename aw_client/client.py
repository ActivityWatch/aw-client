import json
import logging
import socket
import os
import time
import threading
import functools
from collections import namedtuple
from typing import Optional, List
from queue import Queue

import requests as req

from aw_core.models import Event
from aw_core.dirs import get_data_dir

from .config import load_config


# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)
logger = logging.getLogger("aw.client")


# TODO: Should probably use OAuth or something

class ActivityWatchClient:
    def __init__(self, client_name: str, testing=False) -> None:
        logger.setLevel(logging.DEBUG if testing else logging.INFO)
        self.testing = testing

        self.buckets = []  # type: List[Dict[str, str]]
        # self.session = {}

        self.client_name = client_name + ("-testing" if testing else "")
        self.client_hostname = socket.gethostname()

        client_config = load_config()
        configsection = "server" if not testing else "server-testing"

        self.server_hostname = client_config[configsection]["hostname"]
        self.server_port = client_config[configsection]["port"]
        logger.info("aw-server destination set to {}:{}".format(self.server_hostname, self.server_port))

        self.dispatch_thread = PostDispatchThread(self)

    #
    #   Get/Post base requests
    #

    def _post(self, endpoint: str, data: dict) -> Optional[req.Response]:
        headers = {"Content-type": "application/json"}
        url = "http://{}:{}/api/0/{}".format(self.server_hostname, self.server_port, endpoint)
        response = req.post(url, data=json.dumps(data), headers=headers)
        response.raise_for_status()
        return response

    def _get(self, endpoint: str) -> Optional[req.Response]:
        url = "http://{}:{}/api/0/{}".format(self.server_hostname, self.server_port, endpoint)
        response = req.get(url)
        response.raise_for_status()
        return response

    #
    #   Event get/post requests
    #

    def get_events(self, bucket: str) -> List[Event]:
        endpoint = "buckets/{}/events".format(bucket)
        events = self._get(endpoint).json()
        return [Event(**event) for event in events]

    def send_event(self, bucket: str, event: Event):
        endpoint = "buckets/{}/events".format(bucket)
        data = [event.to_json_dict()]
        self.dispatch_thread.add_request(endpoint, data)

    def send_events(self, bucket: str, events: List[Event], ignore_failed=False):
        endpoint = "buckets/{}/events".format(bucket)
        if len(events) <= 0:
            logger.warning("Aborting sending empty list of events")
            return
        data = [event.to_json_dict() for event in events]
        self.dispatch_thread.add_request(endpoint, data)

    def replace_last_event(self, bucket: str, event: Event):
        endpoint = "buckets/{}/events/replace_last".format(bucket)
        data = event.to_json_dict()
        self.dispatch_thread.add_request(endpoint, data)

    def heartbeat(self, bucket, event: Event, pulsetime: float):
        endpoint = "buckets/{}/heartbeat?pulsetime={}".format(bucket, pulsetime)
        data = event.to_json_dict()
        self.dispatch_thread.add_request(endpoint, data)

    #
    #   Bucket get/create/delete requests
    #

    def get_buckets(self):
        return self._get('buckets').json()

    def delete_bucket(self, bucket):
        if not self.testing:
            logger.error("Cannot delete bucket when client and/or server isn't running in testing mode!")
            return False
        try:
            response = self._get("buckets/{}/delete".format(bucket))
            return response.ok
        except req.RequestException as e:
            logger.error(e)
            return False

    def setup_bucket(self, bucket_id: str, event_type: str) -> bool:
        self.buckets.append({"bid": bucket_id, "etype": event_type})

    def _create_buckets(self):
        # Check if bucket exists
        buckets = self.get_buckets()
        success = True
        for bucket in self.buckets:
            if bucket['bid'] not in buckets:
                # Create bucket
                endpoint = "buckets/{}".format(bucket['bid'])
                data = {
                    'client': self.client_name,
                    'hostname': self.client_hostname,
                    'type': bucket['etype'],
                }
                response = self._post(endpoint, data)
                if not response.ok:
                    success = False
        return success

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


def RestartOnException(f):
    @functools.wraps(f)
    def g(*args, **kwargs):
        while True:
            try:
                f(*args, **kwargs)
            except Exception as e:
                logger.error("{} crashed due to exception, restarting.".format(f))
                logger.error(e)
                time.sleep(1)  # To prevent extremely fast restarts in case of bad state.
    return g


class PostDispatchThread(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self, daemon=True)
        self.running = True
        self.connected = False

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
                failed_requests.append(QueuedRequest(*json.loads(request)))

        # Insert failed_requests into dispatching queue
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
            return self.client._create_buckets()
        except req.RequestException:
            return False

    @RestartOnException
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
                request = self._queue.get()
                try:
                    self.client._post(request.endpoint, request.data)
                except req.RequestException as e:
                    self._queue.queue.appendleft(request)
                    self.connected = False
                    logger.warning("Can't connect to aw-server, will queue events until connection is available.")
                    logger.warning(e)

            # Disconnected or self.running set to false, save remaining to queuefile
            self._save_queue()

    def add_request(self, endpoint, data):
        if self.connected:
            self._queue.put(QueuedRequest(endpoint=endpoint, data=data))
        else:
            self._queue_to_file(endpoint, data)
