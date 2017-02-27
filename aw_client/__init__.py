from .generate_config import generate_config

# DEPRECATED: Clients should call generate_config themselves
config = generate_config()

from .client import ActivityWatchClient

