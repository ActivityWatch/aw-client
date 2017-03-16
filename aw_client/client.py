import json
import logging
import socket
import appdirs
import os
import time
import threading
from typing import Optional, List, Union
from queue import Queue

import requests as req

from aw_core.models import Event
from aw_core.dirs import get_data_dir
from .config import client_config

# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)
logger = logging.getLogger("aw.client")


# TODO: Should probably use OAuth or something

class ActivityWatchClient:
    def __init__(self, client_name: str, testing=False):
        logger.setLevel(logging.DEBUG if testing else logging.INFO)
        self.testing = testing

        self.buckets = []
        self.session = {}

        self.client_name = client_name + ("-testing" if testing else "")
        self.client_hostname = socket.gethostname()

        configsection = "server" if not testing else "server-testing"
        self.server_hostname = client_config[configsection]["hostname"]
        self.server_port = client_config[configsection]["port"]
        logger.info("aw-server destination set to {}:{}".format(self.server_hostname, self.server_port))

        # Setup failed queues file
        self.data_dir = get_data_dir("aw-client")
        self.failed_queues_dir = os.path.join(self.data_dir, "failed_events")
        if not os.path.exists(self.failed_queues_dir):
            os.makedirs(self.failed_queues_dir)
        self.queue_file = os.path.join(self.failed_queues_dir, self.client_name)

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

    def get_events(self, bucket) -> List[Event]:
        endpoint = "buckets/{}/events".format(bucket)
        events = self._get(endpoint).json()
        return [Event(**event) for event in events]

    def send_event(self, bucket, event: Event):
        endpoint = "buckets/{}/events".format(bucket)
        data = [event.to_json_dict()]
        self.dispatch_thread.add_request(endpoint, data)

    def send_events(self, bucket, events: List[Event], ignore_failed=False):
        endpoint = "buckets/{}/events".format(bucket)
        if len(events) <= 0:
            logger.warning("Aborting sending empty list of events")
            return
        data = [event.to_json_dict() for event in events]
        self.dispatch_thread.add_request(endpoint, data)

    def replace_last_event(self, bucket, event: Event):
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
        else:
            return self._get("buckets/{}/delete".format(bucket))

    def setup_bucket(self, bucket_id, event_type: str) -> bool:
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


class PostDispatchThread(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self)
        self.daemon = True
        self.running = True
        self.connected = False
        self.client = client
        self.queue = Queue()
        self._load_queue()

    def _queue_failed_request(self, endpoint: str, data: dict):
        # Find failed queue file
        entry = [endpoint, data]
        with open(self.client.queue_file, "a+") as queue_fp:
            queue_fp.write(json.dumps(entry) + "\n")

    def _load_queue(self):
        # If crash when lost connection, queue failed requests
        failed_requests = []
        open(self.client.queue_file, "a").close()  # Create file if doesn't exist
        with open(self.client.queue_file, "r") as queue_fp:
            for request in queue_fp:
                failed_requests.append(json.loads(request))
        open(self.client.queue_file, "w").close()  # Clear file
        if len(failed_requests) > 0:
            logger.info("Adding {} failed events to queue to send".format(len(failed_requests)))
            for request in failed_requests:
                self.queue.put([request[0], request[1]])

    def _save_queue(self):
        # When lost connection, save queue to file for later sending
        with open(self.client.queue_file, "w") as queue_fp:
            for request in self.queue.queue:
                queue_fp.write(json.dumps(request) + "\n")

    def run(self):
        while self.running:
            while not self.connected and self.running:
                try: # Try to connect, else retry in 60s
                    if self.client._create_buckets():
                        self.connected = True
                    else:
                        time.sleep(20)
                except req.RequestException as e:
                    time.sleep(20)
            logger.info("Connection to aw-server established")
            self._load_queue()
            while self.connected and self.running:
                request = self.queue.get()
                try:
                    self.client._post(*request)
                except req.RequestException as e:
                    self.queue.queue.appendleft(request)
                    self.connected = False
                    logger.warning("Can't connect to aw-server, will queue events until connection is available")
            self._save_queue()

    def add_request(self, endpoint, data):
        if self.connected:
            self.queue.put([endpoint, data])
        else:
            self._queue_failed_request(endpoint, data)
