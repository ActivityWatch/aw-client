"""Offline regressions for the interactive redaction example."""

import importlib.util
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aw_core import Event


@pytest.fixture
def redact():
    path = Path(__file__).resolve().parents[1] / "examples" / "redact_sensitive.py"
    spec = importlib.util.spec_from_file_location("redact_sensitive", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalClient:
    def __init__(self):
        self.events = [
            Event(
                id=7,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                duration=1,
                data={"title": "secret", "url": "public"},
            )
        ]
        self.writes = []

    def get_buckets(self):
        return {"test": {}}

    def get_events(self, bucket_id, limit=-1):
        return deepcopy(self.events)

    def delete_event(self, bucket_id, event_id):
        self.writes.append("delete")
        self.events = [e for e in self.events if e.id != event_id]

    def insert_event(self, bucket_id, event):
        self.writes.append("insert")
        raise OSError("injected insert failure")


def test_wet_failure_preserves_original(redact, monkeypatch):
    client = LocalClient()
    before = deepcopy(client.events)
    monkeypatch.setattr(redact, "aw", client, raising=False)
    monkeypatch.setattr(redact, "DRYRUN", False)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    with pytest.raises(RuntimeError, match="No events were modified"):
        redact._redact_bucket("test", "secret")

    assert client.events == before
    assert client.writes == []


def test_regex_input_preserves_nondigit_class(redact, monkeypatch):
    client = LocalClient()
    answers = iter(["test", "regex", r"\D+", ""])
    selected = []
    monkeypatch.setattr(redact.sys, "argv", ["redact_sensitive.py"])
    monkeypatch.setattr(redact, "ActivityWatchClient", lambda **_: client)
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(
        redact, "_redact_bucket", lambda _, pattern: selected.append(pattern)
    )

    redact.main()

    event = deepcopy(client.events[0])
    event.data = {"title": "12345"}
    assert not redact._check_event(event, selected[0])
    assert redact._redact_event(event, selected[0]).data == event.data


@pytest.mark.parametrize(
    "expression,title,matched",
    [
        (r"\D+", "12345", False),
        (r"\D+", "SECRET", True),
        (r"\S+", "   ", False),
        (r"\S+", "12345", True),
        (r"[A-Z]+", "secret", True),
        (r"(?-i:SECRET)", "secret", False),
        (r"(?-i:SECRET)", "SECRET", True),
        (r"^", "12345", True),
    ],
)
def test_regex_selection_and_preview_agree(redact, expression, title, matched):
    event = LocalClient().events[0]
    event.data = {"title": title, "count": 5}
    before = deepcopy(event)
    pattern = re.compile(expression, re.IGNORECASE)

    assert redact._check_event(event, pattern) is matched
    assert redact._find_sensitive([event], pattern) == ({7} if matched else set())
    after = redact._redact_event(event, pattern)
    assert after.data == {
        "title": redact.REDACTED if matched else title,
        "count": 5,
    }
    assert event == before
    assert (after.id, after.timestamp, after.duration) == (
        event.id,
        event.timestamp,
        event.duration,
    )


@pytest.mark.parametrize("wet,confirmation", [(False, "y"), (True, ""), (True, "n")])
def test_dry_run_and_confirmation_do_not_write(redact, monkeypatch, wet, confirmation):
    client = LocalClient()
    before = deepcopy(client.events)
    monkeypatch.setattr(redact, "aw", client, raising=False)
    monkeypatch.setattr(redact, "DRYRUN", not wet)
    monkeypatch.setattr("builtins.input", lambda _: confirmation)

    redact._redact_bucket("test", "secret")

    assert client.events == before
    assert client.writes == []


def test_main_defaults_to_dry_run_and_warns_about_raw_output(
    redact, monkeypatch, capsys
):
    client = LocalClient()
    answers = iter(["test", "string", "SeCrEt", "", "y"])
    monkeypatch.setattr(redact, "DRYRUN", False)
    monkeypatch.setattr(redact.sys, "argv", ["redact_sensitive.py"])
    monkeypatch.setattr(redact, "ActivityWatchClient", lambda **_: client)
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    redact.main()

    assert redact.DRYRUN is True
    assert client.writes == []
    output = capsys.readouterr().out
    assert "Found 1 sensitive events" in output
    assert "'title': 'REDACTED'" in output
    assert "captured logs are not sanitized" in output
