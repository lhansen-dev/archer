"""Registry of configurable applications. Add new apps to ALL_APPS."""

from .ghostty import Ghostty
from .sway import Sway
from .waybar import Waybar
from .openrgb import OpenRGB

ALL_APPS = [Ghostty(), Sway(), Waybar(), OpenRGB()]


def available_apps():
    return [app for app in ALL_APPS if app.available()]
