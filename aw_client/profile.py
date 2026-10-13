"""Profile resolution for aw-client.

A *profile* names an isolated ActivityWatch instance (data, config, port,
queue file). `default` is the ordinary install, `testing` is what
`--testing` has always meant, and any other name (for example `research`)
is a sibling instance that can run at the same time as the others.

The carrier is the ``AW_PROFILE`` environment variable. aw-core's
``_get_appname()`` suffixes the platformdirs root when it is set to a
non-empty value, so exporting the profile here isolates dirs for this
process — without threading a flag through the request queue or REST
client internals.

Kept in sync with aw-qt's ``aw_qt/profile.py`` and aw-server's
``aw_server/profile.py`` (same validation rule as aw-server-rust). One
intentional difference: the default profile *unsets* ``AW_PROFILE``
instead of setting it to ``"default"``. aw-core treats any non-empty
value as a suffix, so ``AW_PROFILE=default`` would resolve to
``activitywatch-default`` and orphan an existing install.

Testing-root note (ActivityWatch/activitywatch#1399): python and rust
resolve ``testing`` identically — new-root-plus-legacy-fallback:

1. If ``activitywatch-testing/`` exists: use it.
2. Else if legacy testing artifacts exist in the bare ``activitywatch/``
   root: stay in legacy mode.
3. Else (fresh setup): create and use ``activitywatch-testing/``.

Isolated roots use bare filenames (``config.toml``). Suffixed names
(``config-testing.toml``) stay legacy-only. The rust API-key lookup in
``config.py`` follows this rule so a ``--testing`` client finds the key
where aw-server-rust actually wrote it.
"""

import os
import re
from typing import Optional

DEFAULT_PROFILE = "default"
TESTING_PROFILE = "testing"

#: Same rule as aw-server-rust's `validate_profile`: lowercase alphanumeric
#: plus `-`/`_`, at most 32 chars, so a profile is always a safe path segment.
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

ENV_VAR = "AW_PROFILE"


def validate_profile(profile: str) -> str:
    """Return the profile unchanged, or raise ValueError if it is not usable."""
    if not PROFILE_RE.match(profile):
        raise ValueError(
            f"Invalid profile name {profile!r}: expected lowercase alphanumeric "
            "with '-' or '_', at most 32 characters"
        )
    return profile


def resolve_profile(profile: Optional[str], testing: bool) -> str:
    """Resolve the effective profile from the CLI flags.

    ``--testing`` is an alias for ``--profile testing``; passing both is only
    an error if they disagree.
    """
    if profile is None:
        return TESTING_PROFILE if testing else DEFAULT_PROFILE

    profile = validate_profile(profile)
    if testing and profile != TESTING_PROFILE:
        raise ValueError(
            f"--testing conflicts with --profile {profile}: --testing is an "
            f"alias for --profile {TESTING_PROFILE}"
        )
    return profile


def is_testing(profile: str) -> bool:
    return profile == TESTING_PROFILE


def profile_suffix(profile: str) -> str:
    """Filename suffix for a profile (``""``, ``"-testing"``, ``"-research"``)."""
    return "" if profile == DEFAULT_PROFILE else f"-{profile}"


def profile_from_env(testing: bool = False) -> str:
    """Read the profile the process was started with.

    Falls back to the `--testing` bool for callers that only track that, so
    behaviour is unchanged when no profile was set.
    """
    profile = os.environ.get(ENV_VAR)
    if not profile:
        return TESTING_PROFILE if testing else DEFAULT_PROFILE
    try:
        return validate_profile(profile)
    except ValueError:
        return TESTING_PROFILE if testing else DEFAULT_PROFILE


def export_profile(profile: str) -> None:
    """Publish the profile to this process and its children.

    The default profile leaves ``AW_PROFILE`` unset so aw-core keeps the
    bare ``activitywatch`` root. Named profiles (including ``testing``)
    set the env var; children inherit it without a CLI flag.
    """
    if profile == DEFAULT_PROFILE:
        os.environ.pop(ENV_VAR, None)
    else:
        os.environ[ENV_VAR] = profile
