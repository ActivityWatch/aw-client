"""
Default classes

Taken from default classes in aw-webui
"""

from typing import List, Dict, Any, Tuple

CategoryId = List[str]
CategorySpec = Dict[str, Any]

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
            "regex": "Messenger|Telegram|Signal|WhatsApp|Rambox|Slack|Riot|Element|Discord|Nheko|NeoChat",
        },
    ),
    (
        ["Comms", "Email"],
        {"type": "regex", "regex": "Gmail|Thunderbird|mutt|alpine"},
    ),
]
