import json
import logging
import socket
import appdirs
import os
import time
import threading
from typing import Optional, List, Union

import requests as req

from aw_core.models import Event
from . import config

# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)
logger = logging.getLogger("aw.client")


# TODO: Should probably use OAuth or something

class ActivityWatchClient:
    def __init__(self, client_name: str, testing=False):
        self.testing = testing

        self.connected = False
        self.buckets = []
        self.session = {}

        self.client_name = client_name + ("-testing" if testing else "")
        self.client_hostname = socket.gethostname()

        self.server_hostname = config["server_hostname"] if not testing else config["testserver_hostname"]
        self.server_port = config["server_port"] if not testing else config["testserver_port"]

        # Setup failed queues dir
        self.data_dir = appdirs.user_data_dir("aw-client")
        self.failed_queues_dir = os.path.join(self.data_dir, "failed_events")
        if not os.path.exists(self.failed_queues_dir):
            os.makedirs(self.failed_queues_dir)
        self.queue_file = os.path.join(self.failed_queues_dir, self.client_name)
        self.queue_file_lock = threading.Lock()

        self.reconnect_thread = None

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
        data = event.to_json_dict()
        if self.connected:
            try:
                self._post(endpoint, data)
                logger.debug("Sent event to server: {}".format(event))
            except req.RequestException as e:
                self._connection_lost()
                logger.warning("Failed to send event to server ({})".format(e))
                self._queue_failed_request(endpoint, data)
        else:
            self._queue_failed_request(endpoint, data)

    def send_events(self, bucket, events: List[Event], ignore_failed=False):
        endpoint = "buckets/{}/events".format(bucket)
        data = [event.to_json_dict() for event in events]
        if self.connected:
            try:
                self._post(endpoint, data)
                logger.debug("Sent events to server: {}".format(events))
            except req.RequestException as e:
                self._connection_lost()
                logger.warning("Failed to send events to server ({})".format(e))
                for event in data:
                    self._queue_failed_request(endpoint, event)
        else:
            self._queue_failed_request(endpoint, data)

    def replace_last_event(self, bucket, event: Event):
        endpoint = "buckets/{}/events/replace_last".format(bucket)
        data = event.to_json_dict()
        if self.connected:
            try:
                self._post(endpoint, data)
                logger.debug("Sent event to server: {}".format(event))
            except req.RequestException as e:
                self._connection_lost()
                logger.warning("Failed to send event to server ({})".format(e))
                self._queue_failed_request(endpoint, data)
        else:
            self._queue_failed_request(endpoint, data)

    #
    #   Bucket get/post requests
    #

    def get_buckets(self):
        return self._get('buckets').json()

    def setup_bucket(self, bucket_id, event_type: str) -> bool:
        self.buckets.append({"bid": bucket_id, "etype": event_type})
        if self.connected:
            self._create_buckets()

    def _create_buckets(self):
        # Check if bucket exists
        buckets = self.get_buckets()
        for bucket in self.buckets:
            if bucket['bid'] in buckets:
                return False  # Don't do anything if bucket already exists
            else:
                # Create bucket
                endpoint = "buckets/{}".format(bucket['bid'])
                data = {
                    'client': self.client_name,
                    'hostname': self.client_hostname,
                    'type': bucket['etype'],
                }
                try:
                    self._post(endpoint, data)
                except req.RequestException as e:
                    logger.error("Failed to create bucket: {}".format(e))
                return True

    #
    #   Failed request queue handling
    #

    def _queue_failed_request(self, endpoint: str, data: dict):
        # Find failed queue file
        entry = {"endpoint": endpoint, "data": data}
        self.queue_file_lock.acquire()
        with open(self.queue_file, "a+") as queue_fp:
            queue_fp.write(json.dumps(entry) + "\n")
        self.queue_file_lock.release()

    def _post_failed_requests(self):
        failed_requests = []
        self.queue_file_lock.acquire()
        with open(self.queue_file, "r") as queue_fp:
            for request in queue_fp:
                failed_requests.append(json.loads(request))
            if len(failed_requests) != 0:
                open(self.queue_file, "w").close()  # Clear file
                logger.info("Sending {} failed events: {}".format(len(failed_requests), failed_requests))
                for request in failed_requests:
                    self._post(request['endpoint'], request['data'])
        self.queue_file_lock.release()

    #
    #   Connection methods
    #

    def connect(self):
        if self._try_connect():
            self.connected = True
        else:
            logger.warning("Can't connect to aw-server, will queue events until connection is available")
            if self.reconnect_thread == None:
                self.reconnect_thread = ReconnectThread(self).start()

    def _try_connect(self):
        try:
            self._create_buckets()
            self._post_failed_requests()
            return True
        except req.RequestException as e:
            return False

    def _connection_lost(self):
        """
            Call this when we lose connection to the server
        """
        self.connected = False
        if self.reconnect_thread == None:
            logger.warning("Connection to aw-server lost, will queue events until connection is available again")
            self.reconnect_thread = ReconnectThread(self).start()


class ReconnectThread(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self)
        self.daemon = True
        self.client = client

    def run(self):
        while not self.client.connected:
            if self.client._try_connect():
                self.client.connected = True
                logger.warning("Connection to aw-server established again")
            else:
                time.sleep(60)
