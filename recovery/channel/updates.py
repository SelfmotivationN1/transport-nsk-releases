"""Keep a saved legacy default from overriding the bundled update channel."""

from importlib import import_module


_released = import_module("_updates_release_123")
_read_config = _released.read_config


def read_config(root):
    config = _read_config(root)
    if config["feed_url"] == config.get("legacy_feed_url"):
        config["feed_url"] = config["channel_feed_url"]
    return config


_released.read_config = read_config


def __getattr__(name):
    return getattr(_released, name)
