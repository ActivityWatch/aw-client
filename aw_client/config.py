from configparser import ConfigParser

from aw_core import dirs
import aw_core.config

default_client_config = ConfigParser()
default_client_config["server"] = {
    "hostname": "localhost",
    "port": "5600",
}
default_client_config["server-testing"] = {
    "hostname": "localhost",
    "port": "5666"
}


def load_config():
    return aw_core.config.load_config("aw-client", default_client_config)
