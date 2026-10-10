import functools
import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import threading
import warnings
from collections import deque, namedtuple
from datetime import datetime
from time import monotonic, sleep
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)

import persistqueue
import requests as req
from aw_core.dirs import get_data_dir
from aw_core.models import Event
from aw_transform.heartbeats import heartbeat_merge

from .config import load_config, load_local_server_api_key
from .profile import (
    DEFAULT_PROFILE,
    export_profile,
    is_testing,
    profile_from_env,
    profile_suffix,
    resolve_profile,
)
from .singleinstance import SingleInstance

# Per-bucket cap on heartbeats held in memory while queue writes fail (e.g. disk full)
_MAX_UNQUEUED_HEARTBEATS = 1000

# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _log_request_exception(e: req.RequestException):
    logger.warning(str(e))
    try:
        d = e.response.json() if e.response else None
        logger.warning(f"Error message received: {d}")
    except json.JSONDecodeError:
        pass


# Explicit None entries override proxies from the environment (HTTP_PROXY,
# HTTPS_PROXY, ALL_PROXY) for a single request, see requests' merge_setting.
_NO_PROXIES: Dict[str, Any] = {"http": None, "https": None, "all": None}


def _is_loopback_host(host: str) -> bool:
    """True if ``host`` names the local machine (localhost, 127.0.0.0/8, ::1)."""
    host = str(host).strip("[]").rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _dt_is_tzaware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


def always_raise_for_request_errors(f: Callable[..., req.Response]):
    @functools.wraps(f)
    def g(*args, **kwargs):
        r = f(*args, **kwargs)
        try:
            r.raise_for_status()
        except req.RequestException as e:
            _log_request_exception(e)
            raise e
        return r

    return g


class ActivityWatchClient:
    def __init__(
        self,
        client_name: str = "unknown",
        testing=False,
        host=None,
        port=None,
        protocol="http",
        profile: Optional[str] = None,
    ) -> None:
        """
        A handy wrapper around the aw-server REST API. The recommended way of interacting with the server.

        Can be used with a `with`-statement as an alternative to manually calling connect and disconnect in a try-finally clause.

        :Example:

        .. literalinclude:: examples/client.py
            :lines: 7-

        ``profile`` selects an isolated instance (config, port, queue file).
        ``testing=True`` is the compat shim for ``profile="testing"``. If
        neither is set, ``AW_PROFILE`` from the launcher is used.
        """
        if profile is not None or testing:
            resolved = resolve_profile(profile, testing)
        else:
            resolved = profile_from_env(False)
        export_profile(resolved)
        self.profile = resolved
        self.testing = is_testing(resolved)

        self.client_name = client_name
        self.client_hostname = socket.gethostname()

        _config = load_config()
        server_key = "server" if resolved == DEFAULT_PROFILE else f"server-{resolved}"
        client_key = "client" if resolved == DEFAULT_PROFILE else f"client-{resolved}"
        if server_key not in _config:
            if resolved != DEFAULT_PROFILE:
                logger.warning(
                    "Profile %s has no [%s] section, falling back to [server] "
                    "(port %s may collide with the default instance)",
                    resolved,
                    server_key,
                    5666 if is_testing(resolved) else 5600,
                )
            server_key = "server"
        if client_key not in _config:
            client_key = "client"
        server_config = _config[server_key]
        client_config = _config[client_key]

        server_host = host or server_config["hostname"]
        server_port = port or server_config["port"]
        self.server_api_key = load_local_server_api_key(
            str(server_host), server_port, profile=resolved
        )
        self.server_address = f"{protocol}://{server_host}:{server_port}"
        # A local server must never be reached through a system proxy: NO_PROXY
        # often lists "localhost" but not "127.0.0.1", which sends every request
        # to the proxy and fails (#41). Remote servers keep honoring the env.
        self._proxies = _NO_PROXIES if _is_loopback_host(server_host) else None

        self.instance = SingleInstance(
            f"{self.client_name}-at-{server_host}-on-{server_port}"
        )

        self.commit_interval = client_config["commit_interval"]

        self.request_queue = RequestQueue(self)
        # Dict of each last heartbeat in each bucket
        self.last_heartbeat = {}  # type: Dict[str, Event]
        # Committed heartbeats whose queue write failed, retried in order before the next commit.
        # Bounded so a sustained failure can't grow memory without limit; the oldest are dropped.
        self._unqueued_heartbeats = {}  # type: Dict[str, Deque[Tuple[str, dict]]]
        self._dropped_heartbeats = {}  # type: Dict[str, int]
        self._warned_queue_before_connect = False

    #
    #   Get/Post base requests
    #

    def _url(self, endpoint: str):
        return f"{self.server_address}/api/0/{endpoint}"

    def _headers(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        request_headers = dict(headers or {})
        if self.server_api_key:
            request_headers.setdefault("Authorization", f"Bearer {self.server_api_key}")
        return request_headers

    @always_raise_for_request_errors
    def _get(self, endpoint: str, params: Optional[dict] = None) -> req.Response:
        return req.get(
            self._url(endpoint),
            params=params,
            headers=self._headers(),
            proxies=self._proxies,
        )

    @always_raise_for_request_errors
    def _post(
        self,
        endpoint: str,
        data: Union[List[Any], Dict[str, Any]],
        params: Optional[dict] = None,
    ) -> req.Response:
        headers = self._headers(
            {"Content-type": "application/json", "charset": "utf-8"}
        )
        return req.post(
            self._url(endpoint),
            data=bytes(json.dumps(data), "utf8"),
            headers=headers,
            params=params,
            proxies=self._proxies,
        )

    @always_raise_for_request_errors
    def _delete(self, endpoint: str, data: Any = None) -> req.Response:
        if data is None:
            data = {}
        headers = self._headers({"Content-type": "application/json"})
        return req.delete(
            self._url(endpoint),
            data=json.dumps(data),
            headers=headers,
            proxies=self._proxies,
        )

    def get_info(self):
        """Returns a dict currently containing the keys 'hostname' and 'testing'."""
        endpoint = "info"
        return self._get(endpoint).json()

    #
    #   Event get/post requests
    #

    def get_event(
        self,
        bucket_id: str,
        event_id: int,
    ) -> Optional[Event]:
        endpoint = f"buckets/{bucket_id}/events/{event_id}"
        try:
            event = self._get(endpoint).json()
            return Event(**event)
        except req.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 404:
                return None
            else:
                raise

    def get_events(
        self,
        bucket_id: str,
        limit: int = -1,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Event]:
        endpoint = f"buckets/{bucket_id}/events"

        params = dict()  # type: Dict[str, str]
        if limit is not None:
            params["limit"] = str(limit)
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()

        events = self._get(endpoint, params=params).json()
        return [Event(**event) for event in events]

    def insert_event(self, bucket_id: str, event: Event) -> Optional[Event]:
        endpoint = f"buckets/{bucket_id}/events"
        data = [event.to_json_dict()]
        response = self._post(endpoint, data)
        if response.json():
            return Event(**response.json()[0])
        return None

    def insert_events(
        self, bucket_id: str, events: List[Event]
    ) -> Optional[List[Event]]:
        endpoint = f"buckets/{bucket_id}/events"
        data = [event.to_json_dict() for event in events]
        response = self._post(endpoint, data)
        if response.json():
            return [Event(**e) for e in response.json()]
        return None

    def delete_event(self, bucket_id: str, event_id: int) -> None:
        endpoint = f"buckets/{bucket_id}/events/{event_id}"
        self._delete(endpoint)

    def get_eventcount(
        self,
        bucket_id: str,
        limit: int = -1,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        endpoint = f"buckets/{bucket_id}/events/count"

        params = dict()  # type: Dict[str, str]
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()

        response = self._get(endpoint, params=params)
        return int(response.text)

    def heartbeat(
        self,
        bucket_id: str,
        event: Event,
        pulsetime: float,
        queued: bool = False,
        commit_interval: Optional[float] = None,
    ) -> None:
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

        endpoint = f"buckets/{bucket_id}/heartbeat?pulsetime={pulsetime}"
        _commit_interval = commit_interval or self.commit_interval

        if queued:
            self._warn_queue_before_connect()
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
                if diff >= _commit_interval:
                    data = merge.to_json_dict()
                    if self._queue_heartbeat(bucket_id, endpoint, data):
                        self.last_heartbeat[bucket_id] = event
                    else:
                        # Keep the merged interval pending; the next heartbeat retries it.
                        self.last_heartbeat[bucket_id] = merge
                else:
                    self.last_heartbeat[bucket_id] = merge
            else:
                data = last_heartbeat.to_json_dict()
                if not self._queue_heartbeat(bucket_id, endpoint, data):
                    # Can't merge with the new event, so hold the old one for retry.
                    unqueued = self._unqueued_heartbeats.setdefault(
                        bucket_id, deque(maxlen=_MAX_UNQUEUED_HEARTBEATS)
                    )
                    if len(unqueued) == unqueued.maxlen:
                        dropped = self._dropped_heartbeats.get(bucket_id, 0)
                        if not dropped:
                            logger.warning(
                                f"Unqueued heartbeat buffer for {bucket_id} is full, dropping oldest heartbeats"
                            )
                        self._dropped_heartbeats[bucket_id] = dropped + 1
                    unqueued.append((endpoint, data))
                self.last_heartbeat[bucket_id] = event
        else:
            self._post(endpoint, event.to_json_dict())

    def _queue_heartbeat(self, bucket_id: str, endpoint: str, data: dict) -> bool:
        """Queue a heartbeat after any earlier ones that failed to queue, preserving order."""
        unqueued = self._unqueued_heartbeats.get(bucket_id, deque())
        while unqueued:
            if not self.request_queue.add_request(*unqueued[0]):
                return False
            unqueued.popleft()
        dropped = self._dropped_heartbeats.pop(bucket_id, 0)
        if dropped:
            logger.warning(
                f"Dropped {dropped} heartbeats for {bucket_id} while the queue was unwritable"
            )
        return self.request_queue.add_request(endpoint, data)

    #
    #   Bucket get/post requests
    #

    def get_buckets(self) -> dict:
        return self._get("buckets/").json()

    def create_bucket(self, bucket_id: str, event_type: str, queued=False):
        if queued:
            self._warn_queue_before_connect()
            self.request_queue.register_bucket(bucket_id, event_type)
        else:
            endpoint = f"buckets/{bucket_id}"
            data = {
                "client": self.client_name,
                "hostname": self.client_hostname,
                "type": event_type,
            }
            self._post(endpoint, data)

    def delete_bucket(self, bucket_id: str, force: bool = False):
        self._delete(f"buckets/{bucket_id}" + ("?force=1" if force else ""))

    # @deprecated
    def setup_bucket(self, bucket_id: str, event_type: str):
        self.create_bucket(bucket_id, event_type, queued=True)

    # Import & export

    def export_all(self) -> dict:
        return self._get("export").json()

    def export_bucket(self, bucket_id) -> dict:
        return self._get(f"buckets/{bucket_id}/export").json()

    def import_bucket(self, bucket: dict) -> None:
        endpoint = "import"
        self._post(endpoint, {"buckets": {bucket["id"]: bucket}})

    #
    #   Query (server-side transformation)
    #

    def query(
        self,
        query: str,
        timeperiods: List[Tuple[datetime, datetime]],
        name: Optional[str] = None,
        cache: bool = False,
    ) -> List[Any]:
        endpoint = "query/"
        params = {}  # type: Dict[str, Any]
        if cache:
            if not name:
                raise Exception(
                    "You are not allowed to do caching without a query name"
                )
            params["name"] = name
            params["cache"] = int(cache)

        # Check that datetimes have timezone information
        for start, stop in timeperiods:
            try:
                assert _dt_is_tzaware(start)
                assert _dt_is_tzaware(stop)
            except AssertionError:
                raise ValueError("start/stop needs to have a timezone set") from None

        data = {
            "timeperiods": [
                "/".join([start.isoformat(), end.isoformat()])
                for start, end in timeperiods
            ],
            "query": query.split("\n"),
        }
        response = self._post(endpoint, data, params=params)
        return response.json()

    #
    # Settings
    #

    def get_setting(self, key: Optional[str] = None) -> dict:
        if key:
            return self._get(f"settings/{key}").json()
        else:
            return self._get("settings").json()

    def set_setting(self, key: str, value: str) -> None:
        self._post(f"settings/{key}", value)

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
        self.request_queue = RequestQueue(
            self, persistqueue_path=self.request_queue.persistqueue_path
        )
        # Reset so warn-before-connect fires again if user calls queued ops before reconnecting
        self._warned_queue_before_connect = False

    def wait_for_start(self, timeout: int = 10) -> None:
        """Wait for the server to start by trying to get the server info."""
        start_time = datetime.now()
        sleep_time = 0.1
        while (datetime.now() - start_time).seconds < timeout:
            try:
                self.get_info()
                break
            except req.exceptions.ConnectionError:
                sleep(sleep_time)
                sleep_time *= 2
        else:
            raise Exception(f"Server at {self.server_address} did not start in time")

    def _warn_queue_before_connect(self) -> None:
        if self._warned_queue_before_connect or self.request_queue.is_alive():
            return

        warnings.warn(
            "Queued requests require calling connect() or using `with client:` "
            "before buckets can be created and queued events can flush.",
            UserWarning,
            stacklevel=3,
        )
        self._warned_queue_before_connect = True

    def wait_for_queue_empty(self, timeout: Optional[float] = None) -> bool:
        """Wait for all queued requests to be sent. See RequestQueue.wait_for_queue_empty."""
        return self.request_queue.wait_for_queue_empty(timeout=timeout)


QueuedRequest = namedtuple("QueuedRequest", ["endpoint", "data"])
Bucket = namedtuple("Bucket", ["id", "type"])

# Bounds for the delay before retrying a queued request after a
# transient server error (e.g. 429/503), honoring Retry-After if given.
RETRY_DELAY_DEFAULT = 0.5
RETRY_DELAY_MAX = 60.0


def _retry_delay(response: req.Response) -> float:
    """Delay before retrying, honoring the Retry-After header (delta-seconds
    form) if present and sane; the HTTP-date form falls back to the default."""
    try:
        delay = float(response.headers.get("Retry-After", RETRY_DELAY_DEFAULT))
    except ValueError:
        return RETRY_DELAY_DEFAULT
    return max(RETRY_DELAY_DEFAULT, min(delay, RETRY_DELAY_MAX))


# Matches the endpoint the queue stores heartbeats under, capturing the bucket
# id and the pulsetime needed to merge consecutive queued heartbeats.
_HEARTBEAT_ENDPOINT_RE = re.compile(
    r"^buckets/(?P<bucket_id>.+)/heartbeat\?pulsetime=(?P<pulsetime>[0-9]+(?:\.[0-9]+)?)$"
)


def _parse_heartbeat_endpoint(endpoint: str) -> Optional[Tuple[str, float]]:
    """Return (bucket_id, pulsetime) for a queued heartbeat endpoint, else None.

    A malformed pulsetime must not raise: it just falls back to sending the
    request verbatim, so one poisoned endpoint cannot wedge the queue.
    """
    match = _HEARTBEAT_ENDPOINT_RE.match(endpoint)
    if match is None:
        return None
    try:
        pulsetime = float(match.group("pulsetime"))
    except ValueError:
        return None
    return match.group("bucket_id"), pulsetime


def _try_event(data: Any) -> Optional[Event]:
    """Parse a queued payload as an Event, or None if it is not one."""
    if not isinstance(data, dict):
        return None
    try:
        return Event(**data)
    except TypeError:
        return None


class RequestQueue(threading.Thread):
    """Used to asynchronously send heartbeats.

    Handles:
        - Cases where the server is temporarily unavailable
        - Saves all queued requests to file in case of a server crash
    """

    VERSION = 1  # update this whenever the queue-file format changes

    # HTTP statuses that indicate a transient server-side problem, for which
    # requests are kept in the queue and retried (dropped on anything else).
    # 503 in particular is sent by aw-server when the heartbeat lock times out.
    RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

    # How many queued requests to pop at once, so consecutive heartbeats for
    # the same bucket can be merged before dispatch.
    BATCH_SIZE = 1000

    def __init__(
        self,
        client: ActivityWatchClient,
        persistqueue_path: Optional[str] = None,
    ) -> None:
        threading.Thread.__init__(self, daemon=True)

        self.client = client

        self.connected = False
        self._stop_event = threading.Event()

        # Buckets that will have events queued to them, will be created if they don't exist
        self._registered_buckets = []  # type: List[Bucket]

        self._attempt_reconnect_interval = 10

        if persistqueue_path is None:
            data_dir = get_data_dir("aw-client")
            queued_dir = os.path.join(data_dir, "queued")
            if not os.path.exists(queued_dir):
                os.makedirs(queued_dir)

            profile = getattr(client, "profile", None)
            suffix = (
                profile_suffix(profile)
                if profile is not None
                else ("-testing" if client.testing else "")
            )
            persistqueue_path = os.path.join(
                queued_dir,
                f"{self.client.client_name}{suffix}.v{self.VERSION}.persistqueue",
            )

        logger.debug(f"queue path '{persistqueue_path}'")
        self.persistqueue_path = persistqueue_path

        self._persistqueue = persistqueue.FIFOSQLiteQueue(
            persistqueue_path, multithreading=True, auto_commit=False
        )
        # The requests popped from the queue but not yet acknowledged. Held
        # across dispatches so a transient error retries them instead of
        # dropping them. A single task_done() deletes every row <= the cursor,
        # i.e. the whole batch, so no per-item bookkeeping is needed.
        self._current_batch = []  # type: List[QueuedRequest]
        # `_current_batch` after coalescing, plus how many of those have been
        # handled. Dispatching one coalesced request per call means a transient
        # error retries only the failed request - never replays ones that
        # already reached the server - and lets the run loop observe stop().
        self._coalesced_batch = []  # type: List[QueuedRequest]
        self._coalesced_index = 0
        self._queue_write_failing = False

    def _get_next(self) -> Optional[QueuedRequest]:
        # Returns the head of the in-flight batch, otherwise pops a single
        # request. Used by tests/callers that only need one item.
        if not self._current_batch:
            try:
                self._current_batch = [self._persistqueue.get(block=False)]
            except persistqueue.exceptions.Empty:
                return None
        return self._current_batch[0]

    def _get_next_batch(self) -> List[QueuedRequest]:
        # Pop up to BATCH_SIZE requests in one go so consecutive heartbeats
        # for the same bucket can be merged before dispatch. The in-memory
        # batch is only acknowledged (deleted) by _task_done(), so a crash
        # mid-batch leaves every popped request on disk for the next run.
        if self._current_batch:
            return self._current_batch
        batch = []  # type: List[QueuedRequest]
        while len(batch) < self.BATCH_SIZE:
            try:
                batch.append(self._persistqueue.get(block=False))
            except persistqueue.exceptions.Empty:
                break
        self._current_batch = batch
        return batch

    def _task_done(self) -> None:
        self._current_batch = []
        self._coalesced_batch = []
        self._coalesced_index = 0
        self._persistqueue.task_done()

    def _create_buckets(self) -> None:
        for bucket in self._registered_buckets:
            self.client.create_bucket(bucket.id, bucket.type)

    def _try_connect(self) -> bool:
        try:  # Try to connect
            self._create_buckets()
            self.connected = True
            logger.info(
                f"Connection to aw-server established by {self.client.client_name}"
            )
        except req.RequestException:
            self.connected = False

        return self.connected

    def wait(self, seconds) -> bool:
        return self._stop_event.wait(seconds)

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def wait_for_queue_empty(self, timeout: Optional[float] = None) -> bool:
        """
        Wait until the queue is empty, or until timeout (in seconds) is reached.

        If the queue thread isn't running nothing can be flushed, so only return
        True when the queue is genuinely empty; requests still pending (e.g. queued
        before connect()) return False.

        :param timeout: max time to wait, in seconds. Waits indefinitely if None.
        :return: True if the queue became empty, False if the timeout was reached.
        """
        if not self.is_alive():
            return self._persistqueue.qsize() == 0 and not self._current_batch

        start_time = monotonic()
        while self._persistqueue.qsize() > 0 or self._current_batch:
            if timeout is not None and monotonic() - start_time >= timeout:
                return False
            if self.wait(0.1):
                # stop() was called while waiting
                return False
        return True

    def _coalesce(self, batch: List[QueuedRequest]) -> List[QueuedRequest]:
        """Merge consecutive queued heartbeats for the same bucket.

        When the server is unreachable the queue accumulates one heartbeat per
        commit interval. Consecutive heartbeats for a bucket usually carry
        identical data, so aw_transform.heartbeat_merge collapses them into a
        handful of long events. Sending the merged events to the heartbeat
        endpoint produces the same server-side result with far fewer requests.

        Requests are grouped by bucket, not by full endpoint: grouping by
        endpoint would reorder a bucket's heartbeats when their pulsetimes
        differ. Within a bucket only a contiguous run sharing one endpoint is
        merged, so the order of a bucket's requests is preserved exactly.
        """
        groups = {}  # type: Dict[str, List[QueuedRequest]]
        for request in batch:
            parsed = _parse_heartbeat_endpoint(request.endpoint)
            key = parsed[0] if parsed is not None else request.endpoint
            groups.setdefault(key, []).append(request)

        coalesced = []  # type: List[QueuedRequest]

        def flush(endpoint: Optional[str], merged: List[Event]) -> None:
            coalesced.extend(
                QueuedRequest(endpoint, event.to_json_dict()) for event in merged
            )

        for requests in groups.values():
            merged = []  # type: List[Event]
            merged_endpoint = None  # type: Optional[str]
            for request in requests:
                parsed = _parse_heartbeat_endpoint(request.endpoint)
                event = _try_event(request.data) if parsed is not None else None
                if (
                    parsed is None
                    or event is None
                    or request.endpoint != merged_endpoint
                ):
                    # Unknown endpoint shape, non-event payload, or a new
                    # endpoint: close the current run and start a new one.
                    flush(merged_endpoint, merged)
                    merged = []
                    merged_endpoint = None
                if parsed is None or event is None:
                    coalesced.append(request)
                    continue
                if merged:
                    merged_event = heartbeat_merge(merged[-1], event, parsed[1])
                    if merged_event is not None:
                        merged[-1] = merged_event
                        continue
                    # Not mergeable: close the run before starting a new one.
                    flush(merged_endpoint, merged)
                    merged = []
                merged.append(event)
                merged_endpoint = request.endpoint
            flush(merged_endpoint, merged)
        return coalesced

    def _dispatch_request(self) -> None:
        batch = self._get_next_batch()
        if not batch:
            self.wait(0.2)  # seconds to wait before re-polling the empty queue
            return

        if not self._coalesced_batch:
            self._coalesced_batch = self._coalesce(batch)
            if not self._coalesced_batch:
                self._task_done()
                return

        # Dispatch one coalesced request per call: a transient error then
        # retries only the failed request (never replays ones that already
        # reached the server), and the run loop can observe stop() between
        # requests instead of blocking on a long batch.
        request = self._coalesced_batch[self._coalesced_index]
        try:
            self.client._post(request.endpoint, request.data)
        except (req.exceptions.ConnectionError, req.exceptions.Timeout):
            # Triggered by:
            #   - server not running (connection refused)
            #   - server not responding (timeout)
            # Keep the batch in memory and go back to waiting for the server
            # to become available (the run loop reconnects).
            self.connected = False
            logger.warning(
                "Connection refused or timeout, will queue requests until connection is available."
            )
            # wait a bit before retrying, so we don't spam the server (or logs), see:
            #  - https://github.com/ActivityWatch/activitywatch/issues/815
            #  - https://github.com/ActivityWatch/activitywatch/issues/756#issuecomment-1266662861
            sleep(0.5)
            return
        except req.RequestException as e:
            # NOTE: `e.response is not None` matters: Response.__bool__ is
            # False for any non-2xx status, so a plain `if e.response` never
            # matches an error response.
            response = e.response
            status_code = response.status_code if response is not None else None
            if response is not None and status_code in self.RETRY_STATUS_CODES:
                # Transient server-side problem (busy, overloaded, restarting
                # or behind a flaky proxy) - the request itself is likely
                # fine, so retry it. Heartbeats are safe to replay: a
                # duplicate of an already-processed heartbeat merges into the
                # last event as a no-op.
                delay = _retry_delay(response)
                logger.warning(
                    f"Server error {status_code}, will retry in {delay}s: {request.endpoint}"
                )
                # stop-aware wait, so a long Retry-After can't block shutdown
                self.wait(delay)
                return
            else:
                # Client errors (e.g. HTTP 400 - bad request, see
                # https://github.com/ActivityWatch/activitywatch/issues/815)
                # are likely to fail forever, so drop this request and move on
                # to the next one.
                logger.error(
                    f"Request failed ({status_code}), not retrying: {request.data}"
                )
        except Exception:
            logger.exception(f"Unknown error, not retrying: {request.data}")

        # Handled (delivered or dropped); advance. Acknowledge the batch only
        # once every request in it has been handled.
        self._coalesced_index += 1
        if self._coalesced_index >= len(self._coalesced_batch):
            self._task_done()

    def run(self) -> None:
        self._stop_event.clear()
        while not self.should_stop():
            # Connect
            while not self._try_connect():
                logger.warning(
                    f"Not connected to server, {self._persistqueue.qsize()} requests in queue"
                )
                if self.wait(self._attempt_reconnect_interval):
                    break

            # Dispatch requests until connection is lost or thread should stop
            while self.connected and not self.should_stop():
                self._dispatch_request()

    def stop(self) -> None:
        self._stop_event.set()

    def add_request(self, endpoint: str, data: dict) -> bool:
        """
        Add a request to the queue.
        Returns False if the request could not be persisted (e.g. disk full),
        so the caller can keep it pending and retry.
        NOTE: Only supports heartbeats
        """
        assert "/heartbeat" in endpoint
        assert isinstance(data, dict)
        try:
            self._persistqueue.put(QueuedRequest(endpoint, data))
        # SQLite reports a full disk as OperationalError, not OSError
        except (OSError, sqlite3.OperationalError) as e:
            # Warn once per failure streak to avoid flooding logs on a full disk.
            if not self._queue_write_failing:
                logger.warning(
                    f"Failed to queue request, possibly due to insufficient disk space: {e}"
                )
                self._queue_write_failing = True
            else:
                logger.debug(f"Failed to queue request (still failing): {e}")
            return False
        if self._queue_write_failing:
            logger.info("Queueing requests succeeded again")
            self._queue_write_failing = False
        return True

    def register_bucket(self, bucket_id: str, event_type: str) -> None:
        bucket = Bucket(bucket_id, event_type)
        self._registered_buckets.append(bucket)

        if not self.connected:
            return

        try:
            self.client.create_bucket(bucket_id, event_type)
        except req.RequestException:
            self.connected = False
