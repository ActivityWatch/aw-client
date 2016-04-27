import os
import shutil
import json
import logging

import appdirs

"""
This module should as it's final result define a variable `config`.

This variable should contain the config generated from defaults and
user-specified configuration in the user_config_dir.

This variable is later imported into the package as aw.client.config
"""

# TODO: Decide if program should use user_config_dir for overriding defaults, or copy defaults there.
#       The former is probably more wise.

logger = logging.getLogger("aw-client.config")

appname = "activitywatch"
user_config_dir = appdirs.user_config_dir(appname)

default_config = {
    "server_hostname": "localhost",
    "server_port": 5600,
    "testserver_hostname": "localhost",
    "testserver_port": 5666,
}

# Deprecated, might be salvaged later
def initialize_config_dirs():
    if not os.path.exists(user_config_dir):
        logger.info("User config dir did not exist, creating")
        os.makedirs(user_config_dir)

    if not os.path.exists(user_config_dir+"/config.json"):
        logger.info("User config file did not exist, creating")
        shutil.copyfile()


def generate_config():
    config_file_path = user_config_dir + "/config.json"
    config = default_config.copy()
    if os.path.exists(config_file_path):
        with open(config_file_path) as f:
            user_config = json.load(f)
        for key in user_config:
            config[key] = user_config[key]

    return config

config = generate_config()
print(config)
