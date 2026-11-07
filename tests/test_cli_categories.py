"""CLI category sources, with real Click callbacks and query builders."""

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from aw_client import ActivityWatchClient, classes, cli

SAVED_CLASSES = [
    {
        "name": ["CustomWork"],
        "rule": {"type": "regex", "regex": "SyntheticEditor"},
    }
]


@pytest.mark.parametrize("command", ["report", "canonical"])
@pytest.mark.parametrize("setting", [SAVED_CLASSES, None, [], "lookup-error"])
@pytest.mark.parametrize(
    "options, expected",
    [
        ([], {"host": None, "port": None, "testing": False, "profile": None}),
        (
            ["--host", "aw.example.test", "--port", "7777"],
            {
                "host": "aw.example.test",
                "port": 7777,
                "testing": False,
                "profile": None,
            },
        ),
        (
            ["--testing"],
            {"host": None, "port": None, "testing": True, "profile": None},
        ),
        (
            ["--profile", "research"],
            {"host": None, "port": None, "testing": False, "profile": "research"},
        ),
    ],
)
def test_commands_resolve_categories_on_selected_client(
    monkeypatch, command, setting, options, expected
):
    selected = Mock(spec=ActivityWatchClient)
    if setting == "lookup-error":
        selected.get_setting.side_effect = RuntimeError(
            "synthetic unavailable settings"
        )
    else:
        selected.get_setting.return_value = setting
    selected.query.return_value = []
    # Distinct fallback client exposes accidental reconstruction, even when
    # environment inheritance happens to select the same profile.
    other = Mock(spec=ActivityWatchClient)
    other.get_setting.return_value = []
    factory = Mock(side_effect=[selected, other])
    monkeypatch.setattr(cli.aw_client, "ActivityWatchClient", factory)

    result = CliRunner().invoke(
        cli.main,
        options
        + [command, "fixture-host", "--start", "2026-10-01", "--stop", "2026-10-02"],
    )

    assert result.exit_code == 0, (result.output, result.exception)
    factory.assert_called_once_with(**expected)
    selected.get_setting.assert_called_once_with("classes")
    other.get_setting.assert_not_called()
    query = selected.query.call_args.args[0]
    if setting == SAVED_CLASSES:
        assert "CustomWork" in query
        assert "SyntheticEditor" in query
    else:
        assert "CustomWork" not in query
        assert "Google Docs|libreoffice|ReText" in query
    assert "aw-watcher-window_fixture-host" in query
    assert "aw-watcher-afk_fixture-host" in query
    assert selected.query.call_args.kwargs == {"cache": False, "name": None}


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("setting", [SAVED_CLASSES, None, [], "lookup-error"])
def test_get_classes_compatibility(monkeypatch, standalone, setting):
    client = Mock(spec=ActivityWatchClient)
    if setting == "lookup-error":
        client.get_setting.side_effect = RuntimeError("synthetic unavailable settings")
    else:
        client.get_setting.return_value = setting
    factory = Mock(return_value=client)
    monkeypatch.setattr(classes.aw_client, "ActivityWatchClient", factory)

    actual = classes.get_classes() if standalone else classes.get_classes(client)

    if setting == SAVED_CLASSES:
        assert actual == [(SAVED_CLASSES[0]["name"], SAVED_CLASSES[0]["rule"])]
    else:
        # Explicit empty settings have historically meant defaults, not an
        # intentionally unclassified query. Preserve that public contract.
        assert actual == classes.default_classes
    client.get_setting.assert_called_once_with("classes")
    assert factory.call_count == int(standalone)
