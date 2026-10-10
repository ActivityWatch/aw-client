"""
Default classes

Taken from default classes in aw-webui
"""

import logging
import random
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
)

import aw_client

logger = logging.getLogger(__name__)

CategoryId = List[str]
CategorySpec = Dict[str, Any]


class SettingsClient(Protocol):
    def get_setting(self, key: str) -> Any: ...


default_classes: List[Tuple[CategoryId, CategorySpec]] = [
    (["Work"], {"type": "regex", "regex": "Google Docs|libreoffice|ReText"}),
    (
        ["Work", "Programming"],
        {
            "type": "regex",
            "regex": "GitHub|Stack Overflow|BitBucket|Gitlab|vim|Spyder|kate|Ghidra|Scite",
        },
    ),
    (
        ["Work", "Programming", "ActivityWatch"],
        {"type": "regex", "regex": "ActivityWatch|aw-", "ignore_case": True},
    ),
    (["Work", "Image"], {"type": "regex", "regex": "Gimp|Inkscape"}),
    (["Work", "Video"], {"type": "regex", "regex": "Kdenlive"}),
    (["Work", "Audio"], {"type": "regex", "regex": "Audacity"}),
    (["Work", "3D"], {"type": "regex", "regex": "Blender"}),
    (["Media", "Games"], {"type": "regex", "regex": "Minecraft|RimWorld"}),
    (["Media", "Video"], {"type": "regex", "regex": "YouTube|Plex|VLC"}),
    (
        ["Media", "Social Media"],
        {
            "type": "regex",
            "regex": "reddit|Facebook|Twitter|Instagram|devRant",
            "ignore_case": True,
        },
    ),
    (
        ["Media", "Music"],
        {"type": "regex", "regex": "Spotify|Deezer", "ignore_case": True},
    ),
    (
        ["Comms", "IM"],
        {
            "type": "regex",
            "regex": "Messenger|Telegram|Signal|WhatsApp|Rambox|Slack|Riot|Discord|Nheko",
        },
    ),
    (
        ["Comms", "Email"],
        {"type": "regex", "regex": "Gmail|Thunderbird|mutt|alpine"},
    ),
]


def get_classes(
    client: Optional[SettingsClient] = None,
) -> List[Tuple[List[str], dict]]:
    """
    Get classes from server-side settings.
    Might throw a 404 if not set yet, in which case we use the default classes as a fallback.
    """
    # Reuse the caller's client when available so host, port, testing mode, and
    # authentication settings stay consistent with the query being performed.
    awc = client or aw_client.ActivityWatchClient(
        f"get-setting-{random.randint(0, 10000)}"
    )
    try:
        classes = awc.get_setting("classes")
    except Exception:
        logger.warning(
            "Failed to get classes from server, using default classes as fallback"
        )
        return default_classes
    if not classes:
        logger.warning(
            "Classes setting is empty/unset, using default classes as fallback"
        )
        return default_classes
    # map into list of tuples
    return [(v["name"], v["rule"]) for v in classes]
