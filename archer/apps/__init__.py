"""Registry of configurable applications. Add new apps to ALL_APPS."""

from .ghostty import Ghostty

ALL_APPS = [Ghostty()]


def available_apps():
    return [app for app in ALL_APPS if app.available()]
