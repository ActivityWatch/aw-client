import logging
import os
from typing import List, Optional, Tuple, Union

import platformdirs
import tomlkit
from aw_core.config import load_config_toml

from .profile import DEFAULT_PROFILE, TESTING_PROFILE, profile_from_env

logger = logging.getLogger(__name__)

_DEFAULT_APPNAME = "activitywatch"
_TESTING_APPNAME = "activitywatch-testing"

# Identical to aw-core#152 / aw-server-rust#652 so python and rust agree on
# the same on-disk state (ActivityWatch/activitywatch#1399). Keep this list
# specific: a false positive would pin a fresh install to legacy forever.
_LEGACY_TESTING_FILENAME_MARKERS = (
    "peewee-sqlite-testing",
    "sqlite-testing",
    "settings-testing",
    "config-testing",
    "-testing.db",
    "-testing.toml",
    "-testing.json",
    "_testing_",
)

default_config = """
[server]
hostname = "127.0.0.1"
port = "5600"

[client]
commit_interval = 10

[server-testing]
hostname = "127.0.0.1"
port = "5666"

[client-testing]
commit_interval = 5
""".strip()


def load_config():
    return load_config_toml("aw-client", default_config)


# Directory resolution must match the server, which writes the files we read
# (e.g. the local API key in aw-server-rust/config.toml). aw-server-rust uses
# the `dirs` crate and aw-core/aw-server use platformdirs: both honor XDG_*
# on Linux and ignore it on macOS/Windows. Do not add XDG_* handling here
# (see #119); in tests, patch these wrappers instead of setting env vars.
def _user_data_dir(appname: str) -> str:
    return platformdirs.user_data_dir(appname)


def _user_config_dir(appname: str) -> str:
    return platformdirs.user_config_dir(appname)


def _user_cache_dir(appname: str) -> str:
    return platformdirs.user_cache_dir(appname)


def _is_legacy_testing_filename(name: str) -> bool:
    lower = name.lower()
    return any(marker in lower for marker in _LEGACY_TESTING_FILENAME_MARKERS)


def _new_testing_root_exists() -> bool:
    """True if any platform dir for ``activitywatch-testing`` already exists.

    Must not create directories: existence is the signal that a previous run
    already adopted the isolated testing root.
    """
    for getter in (_user_data_dir, _user_config_dir, _user_cache_dir):
        if os.path.isdir(getter(_TESTING_APPNAME)):
            return True
    return False


def _legacy_testing_artifacts_exist() -> bool:
    """True if testing data still lives under the shared ``activitywatch`` root."""
    roots = (
        _user_data_dir(_DEFAULT_APPNAME),
        _user_config_dir(_DEFAULT_APPNAME),
        _user_cache_dir(_DEFAULT_APPNAME),
    )
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for filename in filenames:
                if _is_legacy_testing_filename(filename):
                    return True
            # Descend one level (activitywatch/aw-server-rust/...) not further.
            if os.path.relpath(dirpath, root) != ".":
                dirnames.clear()
    return False


def using_legacy_testing_root() -> bool:
    """Whether ``profile=testing`` should stay on the shared ``activitywatch`` root.

    Resolution rule (ActivityWatch/activitywatch#1399), identical on python
    and rust:

    1. If ``activitywatch-testing/`` already exists: use it (new layout).
    2. Else if legacy testing artifacts exist in the bare ``activitywatch/``
       root: stay in legacy mode (old paths, old filenames).
    3. Else (fresh setup): create and use ``activitywatch-testing/``.
    """
    if _new_testing_root_exists():
        return False
    return _legacy_testing_artifacts_exist()


def rust_server_config_candidates(profile: str) -> List[Tuple[str, int]]:
    """Ordered rust config files to read for this profile.

    Isolated profile roots (including new-style ``activitywatch-testing/``)
    use bare ``config.toml`` — the directory already isolates. Suffixed
    ``config-testing.toml`` remains only in the legacy shared-root layout.

    Lookup does **not** create directories (``get_config_dir`` would, and
    creating ``activitywatch-testing/`` would flip the fallback). Testing
    tries the other layout second so a python client that already created
    the new root still finds a rust server that wrote the key on the
    shared root — the regression Erik named on #1399.
    """
    testing_new = (
        os.path.join(
            _user_config_dir(_TESTING_APPNAME), "aw-server-rust", "config.toml"
        ),
        5666,
    )
    testing_legacy = (
        os.path.join(
            _user_config_dir(_DEFAULT_APPNAME),
            "aw-server-rust",
            "config-testing.toml",
        ),
        5666,
    )
    if profile == TESTING_PROFILE:
        if using_legacy_testing_root():
            return [testing_legacy, testing_new]
        return [testing_new, testing_legacy]
    if profile == DEFAULT_PROFILE or not profile:
        return [
            (
                os.path.join(
                    _user_config_dir(_DEFAULT_APPNAME),
                    "aw-server-rust",
                    "config.toml",
                ),
                5600,
            )
        ]
    return [
        (
            os.path.join(
                _user_config_dir(f"{_DEFAULT_APPNAME}-{profile}"),
                "aw-server-rust",
                "config.toml",
            ),
            5600,
        )
    ]


def load_local_server_api_key(
    host: str,
    port: Union[int, str],
    profile: Optional[str] = None,
) -> Optional[str]:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None

    try:
        requested_port = int(str(port))
    except (TypeError, ValueError):
        return None

    if profile is None:
        profile = profile_from_env(False)

    for config_path, default_port in rust_server_config_candidates(profile):
        if not os.path.isfile(config_path):
            continue

        try:
            with open(config_path, encoding="utf-8") as f:
                config = tomlkit.parse(f.read())
            configured_port = int(str(config.get("port", default_port)))
            if configured_port != requested_port:
                continue

            auth_config = config.get("auth", {})
            api_key = auth_config.get("api_key")
            if api_key:
                return str(api_key)
        except Exception as e:
            logger.warning(
                "Failed to read aw-server-rust config %s: %s", config_path, e
            )

    return None
