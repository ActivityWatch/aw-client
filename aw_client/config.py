from configparser import ConfigParser

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

default_client_config["client"] = {
    "commit_interval": "10",
}
default_client_config["client-testing"] = {
    "commit_interval": "5"
}


def load_config():
    return aw_core.config.load_config("aw-client", default_client_config)
