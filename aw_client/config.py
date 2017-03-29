from configparser import ConfigParser

from aw_core import dirs
from aw_core.config import load_config

default_client_config = ConfigParser()
default_client_config["server"] = {
    "hostname": "localhost",
    "port": 5600,
}
default_client_config["server-testing"] = {
    "hostname": "localhost",
    "port": 5666
}

client_config = load_config("aw-client", default_client_config)
