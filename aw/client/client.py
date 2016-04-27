import logging
import requests as req
import socket
import json

logging.getLogger("requests").setLevel(logging.WARNING)

class ActivityWatchClient:
    def __init__(self, clientname, server_hostname="localhost", server_port="5000"):
        self.logger = logging.getLogger("aw-client")

        self.client_name = clientname
        self.client_hostname = socket.gethostname()
        self.server_hostname = server_hostname
        self.server_port = server_port

    def __enter__(self):
        # Should: be used to send a new-session message with eventual client settings etc.
        # Should: Be used to generate a unique session-id to identify the session (hash(time.time() + client_name))
        # Could: be used to generate a session key/authentication
        pass

    def __exit__(self):
        # Should: be used to send a end-session message
        # Could: be used to tell server to discard the session key
        pass

    def _send(self, endpoint, data):
        headers = {"Content-type": "application/json", "Accept": "text/plain"}
        url = "http://{}:{}/{}".format(self.server_hostname, self.server_port, endpoint)
        req.post(url, data=json.dumps(data), headers=headers)

    def send_event(self, event):
        endpoint = "api/0/activity/{}".format(self.client_name)
        self._send(endpoint, event)
        self.logger.debug("Sent activity to server: {}".format(event))

