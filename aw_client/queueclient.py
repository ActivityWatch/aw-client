import json
import os
import time
import threading
from functools import wraps
from typing import List, Union, Callable
from collections import namedtuple, deque

from aw_core.models import Event
from .client import ActivityWatchClient

AWRequest = namedtuple("AWRequest", ["action", "args", "kwargs"])


class ActivityWatchQueueClient(ActivityWatchClient):
    def __init__(self, client_name: str, testing=False, use_queue=True):
        ActivityWatchClient.__init__(self, client_name, testing=testing)

        # Send old failed events
        self._post_failed_events()

        self.dispatcher = RequestDispatcher(self)
        self.dispatcher.start()

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
    def create_bucket(self, bucket_id: str, event_type: str):
        ActivityWatchClient.create_bucket(self, bucket_id, event_type)

    @queue_request
    def send_event(self, bucket: str, event: Union[dict, Event]):
        if isinstance(event, Event):
            event = event.to_json_dict()
        ActivityWatchClient.send_event(self, bucket, event)

    @queue_request
    def send_events(self, bucket: str, events: Union[List[dict], List[Event]]):
        if isinstance(events[0], Event):
            events = [event.to_json_dict() for event in events]
        ActivityWatchClient.send_events(self, bucket, events)

    @queue_request
    def replace_last_event(self, bucket: str, event: Event):
        if isinstance(event, Event):
            event = event.to_json_dict()
        ActivityWatchClient.replace_last_event(self, bucket, event)


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

    def _send_request(self, req: AWRequest):
        """Should only be called by the dispatcher"""
        f = getattr(self.client, req.action)
        f(*req.args, is_queued=True, **req.kwargs)

    def _dispatch(self):
        while len(self._queue) != 0:
            request = self._queue[0]
            # TODO: Check if successful
            success = self._send_request()

            if success:
                # If successful
                self.logger.info("Sent request: {}".format(request))
                self._queue.popleft()
            else:
                # If unsuccessful, stop trying to send requests
                break
