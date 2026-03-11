"""Tests for aw_client.sync."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aw_core.models import Event

from aw_client.sync import AWSync


# ---------------------------------------------------------------------------
# helpers


def _make_event(ts_offset_secs: int = 0, duration_secs: int = 60) -> Event:
    ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(
        seconds=ts_offset_secs
    )
    return Event(
        timestamp=ts,
        duration=timedelta(seconds=duration_secs),
        data={"app": "test"},
    )


def _mock_get_response(buckets: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status = MagicMock()
    m.json.return_value = buckets
    return m


def _mock_post_response(status: int = 201) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.raise_for_status = MagicMock()
    return m


# ---------------------------------------------------------------------------
# fixtures


@pytest.fixture
def mock_local_client() -> MagicMock:
    client = MagicMock()
    client.get_buckets.return_value = {
        "aw-watcher-window_host": {
            "type": "currentwindow",
            "hostname": "host",
        }
    }
    client.get_events.return_value = [_make_event(0), _make_event(60)]
    return client


@pytest.fixture
def sync_obj(mock_local_client: MagicMock, tmp_path: Path) -> AWSync:
    return AWSync(
        sync_url="http://localhost:5667",
        api_key="test-key",
        local_client=mock_local_client,
        state_file=tmp_path / "state.json",
    )


# ---------------------------------------------------------------------------
# tests


class TestAWSync:
    def test_sync_uploads_events(
        self, sync_obj: AWSync, mock_local_client: MagicMock
    ) -> None:
        """Happy path: two events are uploaded, bucket created on server."""
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            mock_get.return_value = _mock_get_response({})  # no remote buckets yet
            mock_post.return_value = _mock_post_response(201)

            results = sync_obj.sync()

        assert results["aw-watcher-window_host"] == 2
        # 1st POST → create bucket; 2nd POST → upload events
        assert mock_post.call_count == 2

    def test_sync_skips_empty_bucket(
        self, sync_obj: AWSync, mock_local_client: MagicMock
    ) -> None:
        """Buckets with no events are skipped; no POST calls made."""
        mock_local_client.get_events.return_value = []

        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            mock_get.return_value = _mock_get_response({})

            results = sync_obj.sync()

        assert results["aw-watcher-window_host"] == 0
        mock_post.assert_not_called()

    def test_sync_filter_by_prefix(
        self, sync_obj: AWSync, mock_local_client: MagicMock
    ) -> None:
        """bucket_filter excludes buckets whose id doesn't match the prefix."""
        with patch("requests.get"), patch("requests.post") as mock_post:
            results = sync_obj.sync(bucket_filter="aw-watcher-afk")

        assert results == {}
        mock_post.assert_not_called()

    def test_state_persisted_after_sync(
        self, sync_obj: AWSync, tmp_path: Path
    ) -> None:
        """State file is written after a successful sync."""
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            mock_get.return_value = _mock_get_response(
                {"aw-watcher-window_host": {"type": "currentwindow"}}
            )
            mock_post.return_value = _mock_post_response(200)

            sync_obj.sync()

        state_file = tmp_path / "state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert "aw-watcher-window_host" in state

    def test_incremental_sync_passes_since(
        self, sync_obj: AWSync, mock_local_client: MagicMock
    ) -> None:
        """On the second sync, get_events is called with a start= argument."""
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            mock_get.return_value = _mock_get_response(
                {"aw-watcher-window_host": {}}
            )
            mock_post.return_value = _mock_post_response(200)

            sync_obj.sync()  # first sync — state saved

        # Second sync — should pass start= based on state
        mock_local_client.get_events.return_value = [_make_event(120)]
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            mock_get.return_value = _mock_get_response(
                {"aw-watcher-window_host": {}}
            )
            mock_post.return_value = _mock_post_response(200)

            sync_obj.sync()

        call_kwargs = mock_local_client.get_events.call_args
        start_arg = call_kwargs.kwargs.get("start")
        assert start_arg is not None, "start= should be passed on second sync"
        assert isinstance(start_arg, datetime)

    def test_error_is_caught_returns_minus_one(
        self, sync_obj: AWSync
    ) -> None:
        """Network errors are caught; the bucket entry is set to -1."""
        with patch("requests.get") as mock_get:
            mock_get.side_effect = Exception("network error")

            results = sync_obj.sync()

        assert results["aw-watcher-window_host"] == -1

    def test_missing_state_file_handled(
        self, mock_local_client: MagicMock, tmp_path: Path
    ) -> None:
        """A missing state file path does not raise during construction."""
        nonexistent = tmp_path / "subdir" / "state.json"
        s = AWSync(
            "http://localhost:5667",
            "key",
            local_client=mock_local_client,
            state_file=nonexistent,
        )
        assert s._state == {}

    def test_existing_remote_bucket_not_recreated(
        self, sync_obj: AWSync, mock_local_client: MagicMock
    ) -> None:
        """If the bucket already exists on the sync server, skip the create POST."""
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            mock_get.return_value = _mock_get_response(
                {"aw-watcher-window_host": {"type": "currentwindow"}}
            )
            mock_post.return_value = _mock_post_response(200)

            sync_obj.sync()

        # Only 1 POST: the events upload (no bucket-creation POST)
        assert mock_post.call_count == 1

    def test_auth_header_sent(
        self, sync_obj: AWSync, mock_local_client: MagicMock
    ) -> None:
        """The Bearer token is included in every request to the sync server."""
        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            mock_get.return_value = _mock_get_response({})
            mock_post.return_value = _mock_post_response(201)

            sync_obj.sync()

        for call in [*mock_get.call_args_list, *mock_post.call_args_list]:
            headers = call.kwargs.get("headers", {})
            assert "Authorization" in headers
            assert headers["Authorization"] == "Bearer test-key"
