import json
import logging
import socket
from collections import defaultdict
from time import time
from typing import Optional

import requests as req

from aw_core.models import Event
from . import config

# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)


# TODO: Make resilient to server crashes/offline connection by storing unsent data locally
#       (temporarily until server is up)

# TODO: Should probably use OAuth or something

class ActivityWatchClient:
    def __init__(self, client_name, testing=False):
        self.logger = logging.getLogger("aw-client")
        self.testing = testing

        self.session = {}

        self.client_name = client_name
        self.client_hostname = socket.gethostname()

        self.server_hostname = config["server_hostname"] if not testing else config["testserver_hostname"]
        self.server_port = config["server_port"] if not testing else config["testserver_port"]

        self.failed_queue = defaultdict(list)

    def __enter__(self):
        # Should: be used to send a new-session message with eventual client settings etc.
        # Should: Be used to generate a unique session-id to identify the session (hash(time.time() + client_name))
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
        session_id = "{}#{}".format(self.client_name, int(time() * 1000))
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

    def _send(self, endpoint: str, data: dict) -> Optional[req.Response]:
        headers = {"Content-type": "application/json"}
        # FIXME: Use HTTPS whenever possible!
        url = "http://{}:{}/api/0/{}/{}".format(self.server_hostname, self.server_port, endpoint, self.client_name)
        response = req.post(url, data=json.dumps(data), headers=headers)
        response.raise_for_status()
        return response

    def _queue_failed(self, endpoint: str, data: dict):
        if not self.testing:
            self.logger.info("Putting data in queue")
            self.failed_queue[endpoint].append(data)
        else:
            raise Exception('Could not contact server')

    def send_event(self, event: Event):
        # TODO: Notice if server responds with invalid session and create a new one
        endpoint = "activity"
        data = event.to_json_dict()
        try:
            self._send(endpoint, data)
            self.logger.debug("Sent activity to server: {}".format(event))
        except req.RequestException as e:
            self.logger.warning("Failed to send event to server ({})".format(e))
            self._queue_failed(endpoint, data)
