"""Registry of configurable applications. Add new apps to ALL_APPS."""

from .ghostty import Ghostty
from .sway import Sway

ALL_APPS = [Ghostty(), Sway()]


def available_apps():
    return [app for app in ALL_APPS if app.available()]
