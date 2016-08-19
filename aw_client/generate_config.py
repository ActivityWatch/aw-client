import os
import json
import logging

import appdirs

"""
This module should as it's final result define a variable `config`.

This variable should contain the config generated from defaults and
user-specified configuration in the user_config_dir.

This variable is later imported into the package as aw_client.config
"""

logger = logging.getLogger("aw.client.config")

appname = "aw-client"
user_config_dir = appdirs.user_config_dir(appname)

default_config = {
    "server_hostname": "localhost",
    "server_port": 5600,
    "testserver_hostname": "localhost",
    "testserver_port": 5666,
}


def generate_config():
    """
    Take the defaults, and if a config file exists, use the settings specified
    there as overrides for their respective defaults.
    """
    # TODO: Add support for individual watcher configs,
    #       such as <user_config_dir>/<watcher>.json
    config_file_path = os.path.join(user_config_dir, "config.json")
    new_config = default_config.copy()
    if os.path.exists(config_file_path):
        with open(config_file_path) as f:
            user_config = json.load(f)
        for key in user_config:
            new_config[key] = user_config[key]

    return new_config

config = generate_config()
logger.info("Configuration used: {}".format(config))
