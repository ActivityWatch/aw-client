import logging
import requests as req
import socket


logging.getLogger("requests").setLevel(logging.WARNING)

class ActivityWatchClient:
    def __init__(self, clientname, server_hostname="localhost", server_port="5000"):
        self.logger = logging.getLogger("actwa-client")

        self.client_name = clientname
        self.client_hostname = socket.gethostname()
        self.server_hostname = server_hostname
        self.server_port = server_port

    def __with__():
        # Should/could be used to generate a session key/authentication
        pass

    def send_event(self, event):
        headers = {"Content-type": "application/json", "Accept": "text/plain"}
        # TODO: Replace {hostname}+{clientname} with {hostname}/{clientname}
        url = "http://{}:{}/api/0/activity/{}+{}".format(self.server_hostname, self.server_port, self.client_hostname, self.hostname)
        req.post(url, data=json.dumps(event), headers=headers)
        self.logger.debug("Sent activity to server: ".format(event))

