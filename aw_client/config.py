import logging
import os
from typing import Optional, Union

import tomlkit
from aw_core import dirs
from aw_core.config import load_config_toml

logger = logging.getLogger(__name__)

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


def load_local_server_api_key(host: str, port: Union[int, str]) -> Optional[str]:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None

    try:
        requested_port = int(str(port))
    except (TypeError, ValueError):
        return None

    config_dir = dirs.get_config_dir("aw-server-rust")
    candidates = (
        ("config.toml", 5600),
        ("config-testing.toml", 5666),
    )

    for filename, default_port in candidates:
        config_path = os.path.join(config_dir, filename)
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
