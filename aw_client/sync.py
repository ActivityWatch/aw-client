"""
Sync client for ActivityWatch — push local events to a self-hosted aw-sync-server.

Usage::

    from aw_client.sync import AWSync

    sync = AWSync(sync_url="http://localhost:5667", api_key="mykey")
    results = sync.sync()         # {bucket_id: events_uploaded}

    # Sync only window-activity buckets
    results = sync.sync(bucket_filter="aw-watcher-window")

See https://github.com/TimeToBuildBob/aw-sync-server for the server implementation.
"""
import json
import logging
import socket
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import requests
from aw_core.models import Event

from .client import ActivityWatchClient

logger = logging.getLogger(__name__)

_DEFAULT_STATE_FILE = Path.home() / ".config" / "activitywatch" / "aw-sync-state.json"


class AWSync:
    """Push ActivityWatch events to a self-hosted aw-sync-server.

    The sync server must implement the standard ActivityWatch bucket+events API
    and accept an ``Authorization: Bearer <api_key>`` header.

    State (last-synced timestamp per bucket) is persisted in a JSON file so that
    incremental syncs only upload new events.
    """

    def __init__(
        self,
        sync_url: str,
        api_key: str,
        local_client: Optional[ActivityWatchClient] = None,
        state_file: Optional[Path] = None,
    ) -> None:
        """
        Args:
            sync_url:     Base URL of the sync server, e.g. ``http://localhost:5667``.
            api_key:      Bearer token for authenticating to the sync server.
            local_client: Optional pre-constructed local AW client; one is created
                          with default settings if not provided.
            state_file:   Path for persisting last-synced timestamps per bucket.
                          Defaults to ``~/.config/activitywatch/aw-sync-state.json``.
        """
        self._base_url = sync_url.rstrip("/") + "/api/0"
        self._auth_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.local = local_client or ActivityWatchClient(client_name="aw-sync")
        self._state_file = state_file or _DEFAULT_STATE_FILE
        self._state: Dict[str, str] = self._load_state()

    # ------------------------------------------------------------------ state

    def _load_state(self) -> Dict[str, str]:
        """Load persisted sync state (last-synced ISO timestamp per bucket)."""
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning("Could not read sync state file; starting fresh")
        return {}

    def _save_state(self) -> None:
        """Persist sync state to disk."""
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._state, indent=2))

    # ------------------------------------------------------- sync-server API

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def _get_remote_buckets(self) -> Dict[str, dict]:
        r = requests.get(self._url("buckets/"), headers=self._auth_headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def _ensure_remote_bucket(
        self, bucket_id: str, event_type: str, hostname: str
    ) -> None:
        """Create bucket on sync server if it doesn't exist yet."""
        r = requests.post(
            self._url(f"buckets/{bucket_id}"),
            json={
                "client": "aw-sync",
                "hostname": hostname,
                "type": event_type,
            },
            headers=self._auth_headers,
            timeout=30,
        )
        # 200 (already exists) and 201 (created) are both fine
        if r.status_code not in (200, 201):
            r.raise_for_status()

    def _upload_events(self, bucket_id: str, events: List[Event]) -> int:
        """Upload a batch of events.  Returns the number of events sent."""
        if not events:
            return 0
        r = requests.post(
            self._url(f"buckets/{bucket_id}/events"),
            json=[e.to_json_dict() for e in events],
            headers=self._auth_headers,
            timeout=60,
        )
        r.raise_for_status()
        return len(events)

    # ----------------------------------------------------------- sync logic

    def sync_bucket(self, bucket_id: str, bucket_info: dict) -> int:
        """Sync one local bucket to the sync server.

        Args:
            bucket_id:   ID of the bucket to sync.
            bucket_info: Metadata dict as returned by ``get_buckets()``.

        Returns:
            Number of events uploaded (0 if nothing new).
        """
        since: Optional[datetime] = None
        if bucket_id in self._state:
            since = datetime.fromisoformat(self._state[bucket_id])

        events = self.local.get_events(bucket_id, start=since)
        if not events:
            return 0

        hostname: str = bucket_info.get("hostname") or socket.gethostname()
        event_type: str = bucket_info.get("type", "unknown")

        remote_buckets = self._get_remote_buckets()
        if bucket_id not in remote_buckets:
            self._ensure_remote_bucket(bucket_id, event_type, hostname)

        count = self._upload_events(bucket_id, events)

        # Advance the high-water mark to the end of the latest event
        latest: datetime = max(
            e.timestamp + (e.duration or timedelta(0)) for e in events
        )
        self._state[bucket_id] = latest.isoformat()
        self._save_state()

        return count

    def sync(self, bucket_filter: Optional[str] = None) -> Dict[str, int]:
        """Sync local ActivityWatch buckets to the sync server.

        Args:
            bucket_filter: Optional prefix; only buckets whose id starts with
                           this string are synced.  Pass e.g. ``"aw-watcher-window"``
                           to sync only window-activity buckets.

        Returns:
            Mapping of ``bucket_id`` → events uploaded.  A value of ``-1``
            indicates that the sync for that bucket failed.
        """
        buckets = self.local.get_buckets()
        results: Dict[str, int] = {}

        for bucket_id, info in buckets.items():
            if bucket_filter and not bucket_id.startswith(bucket_filter):
                continue
            try:
                count = self.sync_bucket(bucket_id, info)
                if count > 0:
                    logger.info("Synced %d events from %s", count, bucket_id)
                results[bucket_id] = count
            except Exception as e:
                logger.error("Failed to sync %s: %s", bucket_id, e)
                results[bucket_id] = -1

        return results
