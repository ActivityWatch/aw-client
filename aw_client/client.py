import json
import logging
import socket
import os
import threading
from datetime import datetime
from collections import namedtuple
from typing import Optional, List, Any, Union, Dict

import requests as req
import persistqueue

from aw_core.models import Event
from aw_core.dirs import get_data_dir
from aw_core.decorators import deprecated

from .config import load_config


# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class ActivityWatchClient:
    """
    A handy wrapper around the aw-server REST API. The recommended way of interacting with the server.

    Can be used with a `with`-statement as an alternative to manually calling connect and disconnect in a try-finally clause.

    :Example:

    .. literalinclude:: examples/client.py
        :lines: 7-
    """

    def __init__(self, client_name: str="unknown", testing=False, host=None) -> None:
        self.testing = testing

        # uses of the client_* variables is deprecated
        self.client_name = client_name
        self.client_hostname = socket.gethostname()

        # use these instead
        self.name = self.client_name
        self.hostname = self.client_hostname

        config = load_config()

        if host:
            self.server_host = host
        else:
            server_config = config["server" if not testing else "server-testing"]
            self.server_host = "{hostname}:{port}".format(**server_config)

        client_config = config["client" if not testing else "client-testing"]
        self.commit_interval = client_config.getfloat("commit_interval")

        self.request_queue = RequestQueue(self)
        # Dict of each last heartbeat in each bucket
        self.last_heartbeat = {}  # type: Dict[str, Event]

    #
    #   Get/Post base requests
    #

    def _url(self, endpoint: str):
        return "http://{host}/api/0/{endpoint}".format(host=self.server_host, endpoint=endpoint)

    def _log_request_exception(self, e: req.RequestException):
        r = e.response
        logger.warning(str(e))
        try:
            d = r.json()
            logger.warning("Error message received: {}".format(d))
        except json.JSONDecodeError:
            pass

    def _get(self, endpoint: str, params: Optional[dict] = None) -> req.Response:
        r = req.get(self._url(endpoint), params=params)
        try:
            r.raise_for_status()
        except req.RequestException as e:
            self._log_request_exception(e)
            raise e
        return r

    def _post(self, endpoint: str, data: Union[List[Any], Dict[str, Any]], params: Optional[dict] = None) -> req.Response:
        headers = {"Content-type": "application/json", "charset": "utf-8"}
        r = req.post(self._url(endpoint), data=bytes(json.dumps(data), "utf8"), headers=headers, params=params)
        try:
            r.raise_for_status()
        except req.RequestException as e:
            self._log_request_exception(e)
            raise e
        return r

    def _delete(self, endpoint: str, data: Any = dict()) -> req.Response:
        headers = {"Content-type": "application/json"}
        r = req.delete(self._url(endpoint), data=json.dumps(data), headers=headers)
        try:
            r.raise_for_status()
        except req.RequestException as e:
            self._log_request_exception(e)
            raise e
        return r

    def get_info(self):
        """Returns a dict currently containing the keys 'hostname' and 'testing'."""
        endpoint = "info"
        return self._get(endpoint).json()

    #
    #   Event get/post requests
    #

    def get_events(self, bucket_id: str, limit: int=100, start: datetime=None, end: datetime=None) -> List[Event]:
        endpoint = "buckets/{}/events".format(bucket_id)

        params = dict()  # type: Dict[str, str]
        if limit is not None:
            params["limit"] = str(limit)
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()

        events = self._get(endpoint, params=params).json()
        return [Event(**event) for event in events]

    # @deprecated  # use insert_event instead
    def send_event(self, bucket_id: str, event: Event):
        return self.insert_event(bucket_id, event)

    # @deprecated  # use insert_events instead
    def send_events(self, bucket_id: str, events: List[Event]):
        return self.insert_events(bucket_id, events)

    def insert_event(self, bucket_id: str, event: Event) -> Event:
        endpoint = "buckets/{}/events".format(bucket_id)
        data = event.to_json_dict()
        return Event(**self._post(endpoint, data).json())

    def insert_events(self, bucket_id: str, events: List[Event]) -> None:
        endpoint = "buckets/{}/events".format(bucket_id)
        data = [event.to_json_dict() for event in events]
        self._post(endpoint, data)

    def get_eventcount(self, bucket_id: str, limit: int=100, start: datetime=None, end: datetime=None) -> int:
        endpoint = "buckets/{}/events/count".format(bucket_id)

        params = dict()  # type: Dict[str, str]
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()

        response = self._get(endpoint, params=params)
        return int(response.text)

    def heartbeat(self, bucket_id: str, event: Event, pulsetime: float, queued: bool=False, commit_interval: Optional[float]=None) -> Optional[Event]:
        """
        Args:
            bucket_id: The bucket_id of the bucket to send the heartbeat to
            event: The actual heartbeat event
            pulsetime: The maximum amount of time in seconds since the last heartbeat to be merged with the previous heartbeat in aw-server
            queued: Use the aw-client queue feature to queue events if client loses connection with the server
            commit_interval: Override default pre-merge commit interval

        NOTE: This endpoint can use the failed requests retry queue.
              This makes the request itself non-blocking and therefore
              the function will in that case always returns None.
        """

        from aw_transform.heartbeats import heartbeat_merge
        endpoint = "buckets/{}/heartbeat?pulsetime={}".format(bucket_id, pulsetime)
        commit_interval = commit_interval if commit_interval else self.commit_interval

        if queued:
            # Pre-merge heartbeats
            if bucket_id not in self.last_heartbeat:
                self.last_heartbeat[bucket_id] = event
                return None

            last_heartbeat = self.last_heartbeat[bucket_id]

            merge = heartbeat_merge(last_heartbeat, event, pulsetime)

            if merge:
                # If last_heartbeat becomes longer than commit_interval
                # then commit, else cache merged.
                diff = (last_heartbeat.duration).total_seconds()
                if diff > commit_interval:
                    data = merge.to_json_dict()
                    self.request_queue.add_request(endpoint, data)
                    self.last_heartbeat[bucket_id] = event
                else:
                    self.last_heartbeat[bucket_id] = merge
            else:
                data = last_heartbeat.to_json_dict()
                self.request_queue.add_request(endpoint, data)
                self.last_heartbeat[bucket_id] = event

            return None
        else:
            return Event(**self._post(endpoint, event.to_json_dict()).json())

    #
    #   Bucket get/post requests
    #

    def get_buckets(self):
        return self._get('buckets/').json()

    def create_bucket(self, bucket_id: str, event_type: str, queued=False):
        if queued:
            self.request_queue.register_bucket(bucket_id, event_type)
        else:
            endpoint = "buckets/{}".format(bucket_id)
            data = {
                'client': self.name,
                'hostname': self.hostname,
                'type': event_type,
            }
            self._post(endpoint, data)

    def delete_bucket(self, bucket_id: str):
        self._delete('buckets/{}'.format(bucket_id))

    @deprecated
    def setup_bucket(self, bucket_id: str, event_type: str):
        self.create_bucket(bucket_id, event_type, queued=True)

    #
    #   Query (server-side transformation)
    #

    def query(self, query: str, start: datetime, end: datetime, name: str=None, cache: bool=False) -> Union[int, dict]:
        endpoint = "query/"
        params = {}  # type: Dict[str, Any]
        if cache:
            if not name:
                raise Exception("You are not allowed to do caching without a query name")
            params["name"] = name
            params["cache"] = int(cache)
        data = {
            'timeperiods': ["/".join([start.isoformat(), end.isoformat()])],
            'query': query.split("\n")
        }
        response = self._post(endpoint, data, params=params)
        if response.text.isdigit():
            return int(response.text)
        else:
            return response.json()

    #
    #   Connect and disconnect
    #

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def connect(self):
        if not self.request_queue.is_alive():
            self.request_queue.start()

    def disconnect(self):
        self.request_queue.stop()
        self.request_queue.join()

        # Throw away old thread object, create new one since same thread cannot be started twice
        self.request_queue = RequestQueue(self)


QueuedRequest = namedtuple("QueuedRequest", ["endpoint", "data"])
Bucket = namedtuple("Bucket", ["id", "type"])


class RequestQueue(threading.Thread):
    """Used to asynchronously send heartbeats.

    Handles:
        - Cases where the server is temporarily unavailable
        - Saves all queued requests to file in case of a server crash
    """

    VERSION = 1  # update this whenever the queue-file format changes

    def __init__(self, client: ActivityWatchClient) -> None:
        threading.Thread.__init__(self, daemon=True)

        self.client = client

        self.connected = False
        self._stop_event = threading.Event()

        # Buckets that will have events queued to them, will be created if they don't exist
        self._registered_buckets = []  # type: List[Bucket]

        self._attempt_reconnect_interval = 10

        # Setup failed queues file
        data_dir = get_data_dir("aw-client")
        queued_dir = os.path.join(data_dir, "queued")
        if not os.path.exists(queued_dir):
            os.makedirs(queued_dir)

        persistqueue_path = os.path.join(
            queued_dir,
            "{}{}.v{}.persistqueue".format(self.client.name, "-testing" if client.testing else "", self.VERSION)
        )
        self._persistqueue = persistqueue.FIFOSQLiteQueue(persistqueue_path, multithreading=True, auto_commit=False)
        self._current = None  # type: Optional[QueuedRequest]

    def _get_next(self) -> Optional[QueuedRequest]:
        # self._current will always hold the next not-yet-sent event,
        # until self._task_done() is called.
        if not self._current:
            try:
                self._current = self._persistqueue.get(block=False)
            except persistqueue.exceptions.Empty:
                return None
        return self._current

    def _task_done(self) -> None:
        self._current = None
        self._persistqueue.task_done()

    def _create_buckets(self) -> None:
        # Check if bucket exists
        buckets = self.client.get_buckets()
        for bucket in self._registered_buckets:
            if bucket.id not in buckets:
                self.client.create_bucket(bucket.id, bucket.type)

    def _try_connect(self) -> bool:
        try:  # Try to connect
            self._create_buckets()
            self.connected = True
            logger.info("Connection to aw-server established by {}".format(self.client.client_name))
        except req.RequestException:
            self.connected = False

        return self.connected

    def wait(self, seconds) -> bool:
        return self._stop_event.wait(seconds)

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def _dispatch_request(self) -> None:
        request = self._get_next()
        if not request:
            self.wait(0.1)  # seconds to wait before re-polling the empty queue
            return

        try:
            self.client._post(request.endpoint, request.data)
        except req.RequestException as e:
            self.connected = False
            logger.warning("Failed to send request to aw-server, will queue requests until connection is available.")
            return

        self._task_done()

    def run(self) -> None:
        self._stop_event.clear()
        while not self.should_stop():
            # Connect
            while not self._try_connect():
                logger.warning("Not connected to server, {} requests in queue".format(self._persistqueue.qsize()))
                if self.wait(self._attempt_reconnect_interval):
                    break

            # Dispatch requests until connection is lost or thread should stop
            while self.connected and not self.should_stop():
                self._dispatch_request()

    def stop(self) -> None:
        self._stop_event.set()

    def add_request(self, endpoint: str, data: dict) -> None:
        """
        Add a request to the queue.
        NOTE: Only supports heartbeats
        """
        assert "/heartbeat" in endpoint
        assert isinstance(data, dict)
        self._persistqueue.put(QueuedRequest(endpoint, data))

    def register_bucket(self, bucket_id: str, event_type: str) -> None:
        self._registered_buckets.append(Bucket(bucket_id, event_type))
