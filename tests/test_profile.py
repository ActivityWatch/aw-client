"""Unit tests for profile resolution."""

import os

import pytest

from aw_client.profile import (
    DEFAULT_PROFILE,
    TESTING_PROFILE,
    export_profile,
    is_testing,
    profile_from_env,
    profile_suffix,
    resolve_profile,
    validate_profile,
)


class TestResolveProfile:
    def test_no_flags_gives_default(self):
        assert resolve_profile(None, False) == DEFAULT_PROFILE

    def test_testing_flag_is_an_alias(self):
        assert resolve_profile(None, True) == TESTING_PROFILE

    def test_explicit_profile_wins(self):
        assert resolve_profile("research", False) == "research"

    def test_testing_with_matching_profile_is_allowed(self):
        assert resolve_profile("testing", True) == TESTING_PROFILE

    def test_testing_with_conflicting_profile_raises(self):
        with pytest.raises(ValueError, match="conflicts"):
            resolve_profile("research", True)

    @pytest.mark.parametrize(
        "name", ["Research", "with space", "a" * 33, "", "../escape", "-leading"]
    )
    def test_invalid_names_rejected(self, name):
        with pytest.raises(ValueError):
            validate_profile(name)

    @pytest.mark.parametrize("name", ["research", "aw2", "my_profile", "my-profile"])
    def test_valid_names_accepted(self, name):
        assert validate_profile(name) == name


class TestProfileSuffix:
    def test_default_has_no_suffix(self):
        assert profile_suffix(DEFAULT_PROFILE) == ""

    def test_testing_keeps_legacy_suffix(self):
        assert profile_suffix(TESTING_PROFILE) == "-testing"

    def test_custom_profile_suffix(self):
        assert profile_suffix("research") == "-research"

    def test_suffixes_are_disjoint(self):
        suffixes = {profile_suffix(p) for p in ("default", "testing", "research")}
        assert len(suffixes) == 3


class TestIsTesting:
    def test_only_testing_profile_is_testing(self):
        assert is_testing(TESTING_PROFILE)
        assert not is_testing(DEFAULT_PROFILE)
        assert not is_testing("research")


class TestExportProfile:
    def test_exported_for_child_processes(self, monkeypatch):
        monkeypatch.delenv("AW_PROFILE", raising=False)
        export_profile("research")
        assert os.environ["AW_PROFILE"] == "research"

    def test_default_unsets_env_so_aw_core_keeps_bare_root(self, monkeypatch):
        monkeypatch.setenv("AW_PROFILE", "research")
        export_profile(DEFAULT_PROFILE)
        assert "AW_PROFILE" not in os.environ


class TestProfileFromEnv:
    def test_defaults_when_unset(self, monkeypatch):
        monkeypatch.delenv("AW_PROFILE", raising=False)
        assert profile_from_env(False) == DEFAULT_PROFILE
        assert profile_from_env(True) == TESTING_PROFILE

    def test_reads_exported_profile(self, monkeypatch):
        monkeypatch.setenv("AW_PROFILE", "research")
        assert profile_from_env(False) == "research"

    def test_ignores_invalid_env_value(self, monkeypatch):
        monkeypatch.setenv("AW_PROFILE", "Not A Profile")
        assert profile_from_env(False) == DEFAULT_PROFILE
