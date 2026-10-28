"""Query display tests run offline, without client configuration or HTTP."""

import copy
import json
from types import SimpleNamespace

import pytest
from aw_datastore import Datastore
from aw_datastore.storages import MemoryStorage
from aw_query import query2
from click.testing import CliRunner

from aw_client.cli import query


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("1", 1),
        ("true", True),
        ('"hello 世界"', "hello 世界"),
        ('{"total": 60}', {"total": 60}),
        ("{}", {}),
        ("[1, 2]", [1, 2]),
        ('[{"total": 60}]', [{"total": 60}]),
        ('[[1], {"total": 60}]', [[1], {"total": 60}]),
    ],
)
@pytest.mark.parametrize("json_output", [False, True])
def test_query_displays_real_aql_results(tmp_path, expression, expected, json_output):
    store = Datastore(MemoryStorage, testing=True)

    def evaluate(text, periods, **kwargs):
        return [
            query2.query("test", text, start, stop, store) for start, stop in periods
        ]

    path = tmp_path / "query.awq"
    path.write_text(f"RETURN = {expression};", encoding="utf-8")
    result = CliRunner().invoke(
        query,
        [str(path), *(["--json"] if json_output else [])],
        obj=SimpleNamespace(client=SimpleNamespace(query=evaluate)),
    )

    assert result.exit_code == 0, repr(result.exception)
    assert json.loads(result.output) == ([expected] if json_output else expected)


def invoke_result(tmp_path, periods, *args):
    path = tmp_path / "query.awq"
    path.write_text("RETURN = [];", encoding="utf-8")
    return CliRunner().invoke(
        query,
        [str(path), *args],
        obj=SimpleNamespace(client=SimpleNamespace(query=lambda *a, **kw: periods)),
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        1.5,
        [{"duration": "not a number", "data": {}}],
        [{"duration": 1, "data": "not an event"}],
        [{"duration": True, "data": {}}],
        [{"duration": 1, "data": {}}, 2],
    ],
)
def test_query_displays_other_json_values(tmp_path, value):
    periods = [value]
    original = copy.deepcopy(periods)
    result = invoke_result(tmp_path, periods)
    assert result.exit_code == 0, repr(result.exception)
    assert json.loads(result.output) == value
    assert periods == original


def test_query_preserves_empty_event_list_display(tmp_path):
    result = invoke_result(tmp_path, [[]])
    assert result.exit_code == 0, repr(result.exception)
    assert result.output == "Showing 10 out of 0 events:\nTotal duration:\t 0:00:00\n"


@pytest.mark.parametrize("json_output", [False, True])
def test_query_preserves_event_preview_totals_and_input(tmp_path, json_output):
    events = [
        {
            "id": i,
            "timestamp": "2026-01-01T12:00:00+00:00",
            "duration": 60.5,
            "data": {"app": f"app-{i}"},
        }
        for i in range(11)
    ]
    original = copy.deepcopy(events)
    result = invoke_result(tmp_path, [events], *(["--json"] if json_output else []))
    assert result.exit_code == 0, repr(result.exception)
    if json_output:
        assert json.loads(result.output) == [original]
    else:
        assert result.output == (
            "Showing 10 out of 11 events:\n"
            + "".join(
                f" - Duration: 0:01:00 \tData: {{'app': 'app-{i}'}}\n"
                for i in range(10)
            )
            + "Total duration:\t 0:11:05.500000\n"
        )
    assert events == original


def test_query_event_projection_without_id_is_not_mutated(tmp_path):
    events = [{"duration": 1, "data": {"app": "test"}}]
    original = copy.deepcopy(events)
    result = invoke_result(tmp_path, [events])
    assert result.exit_code == 0, repr(result.exception)
    assert "Total duration:\t 0:00:01" in result.output
    assert events == original


def test_query_dispatches_each_period_independently(tmp_path):
    result = invoke_result(tmp_path, [1, [], {"total": 60}])
    assert result.exit_code == 0, repr(result.exception)
    assert result.output == (
        '1\nShowing 10 out of 0 events:\nTotal duration:\t 0:00:00\n{"total": 60}\n'
    )
