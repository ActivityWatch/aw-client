import json
import logging
import socket
import appdirs
import os
import time
import threading
from collections import defaultdict
from typing import Optional, List, Union

import requests as req

from aw_core.models import Event
from . import config

# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)


# TODO: Make resilient to server crashes/offline connection by storing unsent data locally
#       (temporarily until server is up)

# TODO: Should probably use OAuth or something

class ActivityWatchClient:
    def __init__(self, bucket_name, testing=False):
        self.logger = logging.getLogger("aw-client")
        self.testing = testing

        self.session = {}

        self.bucket_name = bucket_name
        self.client_hostname = socket.gethostname()

        self.server_hostname = config["server_hostname"] if not testing else config["testserver_hostname"]
        self.server_port = config["server_port"] if not testing else config["testserver_port"]

        # Setup failed queues dir
        data_dir = appdirs.user_data_dir("aw-client")
        self.failed_queues_dir = "{directory}/failed_events".format(directory=data_dir)
        if not os.path.exists(self.failed_queues_dir):
            os.makedirs(self.failed_queues_dir)
        
        # Find failed queue file
        self.queue_file = "{directory}/{bucket}.jsonl".format(directory=self.failed_queues_dir, bucket=self.bucket_name)
        
        # Send old failed events
        QueueTimerThread(self).start()

    def __enter__(self):
        # Should: be used to send a new-session message with eventual client settings etc.
        # Should: Be used to generate a unique session-id to identify the session (hash(time.time() + bucket_name))
        # Could: be used to generate a session key/authentication
        self._start_session()
        if self.session:
            self.logger.info("Started session")
        else:
            self.logger.error("Failed to start session")

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Should: be used to send a end-session message
        # Could: be used to tell server to discard the session key
        self.session_active = False

    def _start_session(self):
        session_id = "{}#{}".format(self.bucket_name, int(time.time() * 1000))
        try:
            resp = self._send("session/start", {"session_id": session_id})
            data = resp.json()
            self.session = {"session_id": session_id, "session_key": data["session_key"]}
        except req.RequestException as e:
            self.logger.error("Could not start session: {}".format(e))

    def _stop_session(self):
        assert self.session_active
        resp = self._send("session/stop", self.session)
        if resp:
            self.session = {}

    def _queue_failed_event(self, endpoint: str, data: dict):
        with open(self.queue_file, "a+") as queue_fp:
            queue_fp.write(json.dumps(data)+"\n")

    def _send_failed_events(self):
        if os.path.exists(self.queue_file):
            failed_events = []
            with open(self.queue_file, "r") as queue_fp:
                queue_fp.seek(0, 0)
                for event in queue_fp:
                    failed_events.append(Event(**json.loads(event)))
                print(failed_events)
            open(self.queue_file, "w").close() # Truncate file
            self.send_events(failed_events)

    def _send(self, endpoint: str, data: dict) -> Optional[req.Response]:
        headers = {"Content-type": "application/json"}
        # FIXME: Use HTTPS whenever possible!
        url = "http://{}:{}/api/0/{}".format(self.server_hostname, self.server_port, endpoint)
        response = req.post(url, data=json.dumps(data), headers=headers)
        response.raise_for_status()
        return response

    def send_event(self, event: Union[Event, List[Event]]):
        # TODO: Notice if server responds with invalid session and create a new one
        endpoint = "buckets/{}/events".format(self.bucket_name)
        data = event.to_json_dict()
        try:
            self._send(endpoint, data)
            self.logger.debug("Sent event to server: {}".format(event))
        except req.RequestException as e:
            self.logger.warning("Failed to send event to server ({})".format(e))
            self._queue_failed_event(endpoint, data)

    def send_events(self, events: List[Event]):
        # TODO: Notice if server responds with invalid session and create a new one
        endpoint = "buckets/{}/events".format(self.bucket_name)
        data = [event.to_json_dict() for event in events]
        try:
            self._send(endpoint, data)
            self.logger.debug("Sent events to server: {}".format(events))
        except req.RequestException as e:
            self.logger.warning("Failed to send events to server ({})".format(e))
            for event in data:
                self._queue_failed_event(endpoint, event)


class QueueTimerThread(threading.Thread):
    def __init__(self, aw_client):
        threading.Thread.__init__(self)
        self.daemon = True
        self.aw_client = aw_client

    def run(self):
        while True:
            self.aw_client._send_failed_events()
            time.sleep(180)

