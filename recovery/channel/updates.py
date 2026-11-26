"""Keep a saved legacy default from overriding the bundled update channel."""

from importlib import import_module
from pathlib import Path
import sys


_released = import_module("_updates_release_123")
_read_config = _released.read_config
_installed = _released.installed


def read_config(root):
    config = _read_config(root)
    if config["feed_url"] == config.get("legacy_feed_url"):
        config["feed_url"] = config["channel_feed_url"]
    return config


_released.read_config = read_config


def has_update_target():
    return _installed() or (
        getattr(sys, "frozen", False)
        and Path(sys.executable).name.casefold() == "Транспорт НСК.exe".casefold()
    )


# In the bundled controller this predicate only controls whether it sends /DIR.
# Use the directory of the executable, even if its workspace is somewhere else.
_released.installed = has_update_target


def __getattr__(name):
    return getattr(_released, name)
