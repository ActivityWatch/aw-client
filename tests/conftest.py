import os

import pytest


@pytest.fixture(autouse=True)
def _clear_aw_profile_after_test():
    """export_profile() writes os.environ directly; monkeypatch.delenv does
    not record an undo when the var was already unset, so later tests would
    inherit a leftover profile."""
    yield
    os.environ.pop("AW_PROFILE", None)
