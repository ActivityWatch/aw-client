"""Client constructor, config sections, persistqueue, and rust api-key lookup."""

import logging
import os
from pathlib import Path

import pytest

from aw_client import ActivityWatchClient
from aw_client import client as client_module
from aw_client.config import load_local_server_api_key, rust_server_config_candidates
from aw_client.profile import DEFAULT_PROFILE, TESTING_PROFILE


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("AW_PROFILE", raising=False)
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: object())
    return tmp_path


class TestClientConstructor:
    def test_testing_alias_uses_testing_port_and_exports_env(self, isolated_dirs):
        client = ActivityWatchClient("t", testing=True)
        assert client.profile == TESTING_PROFILE
        assert client.testing is True
        assert client.server_address.endswith(":5666")
        assert os.environ["AW_PROFILE"] == "testing"

    def test_explicit_profile_testing_is_the_same(self, isolated_dirs):
        client = ActivityWatchClient("t", profile="testing")
        assert client.profile == TESTING_PROFILE
        assert client.testing is True
        assert client.server_address.endswith(":5666")

    def test_default_unsets_env_and_uses_5600(self, isolated_dirs, monkeypatch):
        monkeypatch.setenv("AW_PROFILE", "research")
        client = ActivityWatchClient("t", profile="default")
        assert client.profile == DEFAULT_PROFILE
        assert client.testing is False
        assert client.server_address.endswith(":5600")
        assert "AW_PROFILE" not in os.environ

    def test_named_profile_falls_back_to_server_section(self, isolated_dirs, caplog):
        with caplog.at_level(logging.WARNING, logger="aw_client.client"):
            client = ActivityWatchClient("t", profile="research")
        assert client.profile == "research"
        assert client.testing is False
        assert client.server_address.endswith(":5600")
        assert os.environ["AW_PROFILE"] == "research"
        assert any(
            "falling back to [server]" in rec.message
            and "may collide with the default instance" in rec.message
            for rec in caplog.records
        )

    def test_env_from_launcher_is_used_when_no_flags(self, isolated_dirs, monkeypatch):
        monkeypatch.setenv("AW_PROFILE", "research")
        client = ActivityWatchClient("t")
        assert client.profile == "research"
        assert os.environ["AW_PROFILE"] == "research"

    def test_explicit_testing_overrides_stale_env(self, isolated_dirs, monkeypatch):
        monkeypatch.setenv("AW_PROFILE", "research")
        client = ActivityWatchClient("t", testing=True)
        assert client.profile == TESTING_PROFILE
        assert os.environ["AW_PROFILE"] == "testing"

    def test_host_and_port_kwargs_still_win(self, isolated_dirs):
        client = ActivityWatchClient(
            "t", profile="research", host="127.0.0.1", port=5667
        )
        assert client.server_address == "http://127.0.0.1:5667"

    def test_conflicting_flags_raise(self, isolated_dirs):
        with pytest.raises(ValueError, match="conflicts"):
            ActivityWatchClient("t", testing=True, profile="research")


class TestPersistqueueSuffix:
    def test_testing_keeps_legacy_suffix(self, isolated_dirs):
        client = ActivityWatchClient("aw-test-client", testing=True)
        assert "aw-test-client-testing." in client.request_queue.persistqueue_path

    def test_named_profile_suffix_is_disjoint(self, isolated_dirs):
        testing = ActivityWatchClient("aw-test-client", testing=True)
        research = ActivityWatchClient("aw-test-client", profile="research")
        assert (
            testing.request_queue.persistqueue_path
            != research.request_queue.persistqueue_path
        )
        assert "aw-test-client-research." in research.request_queue.persistqueue_path

    def test_reconnect_preserves_named_profile_queue_path(
        self, isolated_dirs, monkeypatch
    ):
        def profile_data_dir(module):
            profile = os.environ.get("AW_PROFILE")
            root = "activitywatch" if profile is None else f"activitywatch-{profile}"
            return str(isolated_dirs / root / module)

        monkeypatch.setattr(client_module, "get_data_dir", profile_data_dir)
        research = ActivityWatchClient("aw-test-client", profile="research")
        original_path = research.request_queue.persistqueue_path
        monkeypatch.setattr(research.request_queue, "_try_connect", lambda: True)
        research.request_queue.connected = True
        research.connect()

        default = ActivityWatchClient("other-client", profile="default")
        assert default.request_queue.persistqueue_path != original_path
        research.disconnect()

        assert research.request_queue.persistqueue_path == original_path


@pytest.fixture
def fake_platform_dirs(tmp_path, monkeypatch):
    """Point rust-config lookup at a tmp tree. platformdirs on macOS/Windows
    ignores XDG_* even when set, so patch the wrappers rather than env vars.
    """
    data = tmp_path / "data"
    config = tmp_path / "config"
    cache = tmp_path / "cache"

    def _join(root: Path, appname: str) -> str:
        return str(root / appname)

    monkeypatch.setattr(
        "aw_client.config._user_data_dir", lambda appname: _join(data, appname)
    )
    monkeypatch.setattr(
        "aw_client.config._user_config_dir",
        lambda appname: _join(config, appname),
    )
    monkeypatch.setattr(
        "aw_client.config._user_cache_dir",
        lambda appname: _join(cache, appname),
    )
    monkeypatch.delenv("AW_PROFILE", raising=False)
    return {"data": data, "config": config, "cache": cache, "root": tmp_path}


def _write_rust_config(
    config_root: Path, appname: str, filename: str, content: str
) -> Path:
    rust_dir = config_root / appname / "aw-server-rust"
    rust_dir.mkdir(parents=True, exist_ok=True)
    path = rust_dir / filename
    path.write_text(content)
    return path


def test_load_local_server_api_key_named_profile(fake_platform_dirs):
    config = fake_platform_dirs["config"]
    _write_rust_config(
        config,
        "activitywatch-research",
        "config.toml",
        'port = 5667\n\n[auth]\napi_key = "research-secret"\n',
    )
    _write_rust_config(
        config,
        "activitywatch",
        "config.toml",
        'port = 5600\n\n[auth]\napi_key = "default-secret"\n',
    )
    # Suffixed names in the isolated root must not be read — dir isolation
    # is the point (ActivityWatch/activitywatch#1399).
    _write_rust_config(
        config,
        "activitywatch-research",
        "config-research.toml",
        'port = 5667\n\n[auth]\napi_key = "suffixed-must-be-ignored"\n',
    )
    assert (
        load_local_server_api_key("127.0.0.1", 5667, profile="research")
        == "research-secret"
    )
    assert load_local_server_api_key("127.0.0.1", 5600, profile="default") == (
        "default-secret"
    )
    assert load_local_server_api_key("127.0.0.1", 5600, profile="research") is None


class TestTestingRootApiKeyLookup:
    """Rust API-key lookup follows the #1399 testing-root rule."""

    def test_fresh_setup_reads_bare_config_in_new_root(self, fake_platform_dirs):
        _write_rust_config(
            fake_platform_dirs["config"],
            "activitywatch-testing",
            "config.toml",
            'port = 5666\n\n[auth]\napi_key = "new-root-secret"\n',
        )
        assert (
            load_local_server_api_key("127.0.0.1", 5666, profile="testing")
            == "new-root-secret"
        )
        paths = [p for p, _ in rust_server_config_candidates("testing")]
        assert paths[0].endswith(
            os.path.join("activitywatch-testing", "aw-server-rust", "config.toml")
        )

    def test_legacy_artifacts_keep_suffixed_shared_root(self, fake_platform_dirs):
        _write_rust_config(
            fake_platform_dirs["config"],
            "activitywatch",
            "config-testing.toml",
            'port = 5666\n\n[auth]\napi_key = "legacy-secret"\n',
        )
        assert (
            load_local_server_api_key("127.0.0.1", 5666, profile="testing")
            == "legacy-secret"
        )
        paths = [p for p, _ in rust_server_config_candidates("testing")]
        assert paths[0].endswith(
            os.path.join("activitywatch", "aw-server-rust", "config-testing.toml")
        )

    def test_new_root_wins_over_legacy_artifacts(self, fake_platform_dirs):
        _write_rust_config(
            fake_platform_dirs["config"],
            "activitywatch",
            "config-testing.toml",
            'port = 5666\n\n[auth]\napi_key = "legacy-secret"\n',
        )
        _write_rust_config(
            fake_platform_dirs["config"],
            "activitywatch-testing",
            "config.toml",
            'port = 5666\n\n[auth]\napi_key = "new-root-secret"\n',
        )
        assert (
            load_local_server_api_key("127.0.0.1", 5666, profile="testing")
            == "new-root-secret"
        )

    def test_empty_new_root_still_finds_legacy_key(self, fake_platform_dirs):
        """Python creating activitywatch-testing/ must not hide rust's legacy key."""
        (fake_platform_dirs["config"] / "activitywatch-testing").mkdir(parents=True)
        _write_rust_config(
            fake_platform_dirs["config"],
            "activitywatch",
            "config-testing.toml",
            'port = 5666\n\n[auth]\napi_key = "legacy-secret"\n',
        )
        assert (
            load_local_server_api_key("127.0.0.1", 5666, profile="testing")
            == "legacy-secret"
        )


class TestCliPortOverride:
    def test_explicit_port_5600_is_not_discarded(self, monkeypatch):
        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def get_buckets(self):
                return {}

        monkeypatch.setattr("aw_client.cli.aw_client.ActivityWatchClient", FakeClient)
        from click.testing import CliRunner

        from aw_client.cli import main

        result = CliRunner().invoke(main, ["--port", "5600", "buckets"])
        assert result.exit_code == 0, result.output
        assert captured["port"] == 5600

    def test_omitted_port_lets_profile_config_win(self, monkeypatch):
        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def get_buckets(self):
                return {}

        monkeypatch.setattr("aw_client.cli.aw_client.ActivityWatchClient", FakeClient)
        from click.testing import CliRunner

        from aw_client.cli import main

        result = CliRunner().invoke(main, ["--testing", "buckets"])
        assert result.exit_code == 0, result.output
        assert captured["port"] is None
        assert captured["testing"] is True
