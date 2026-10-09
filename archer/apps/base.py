"""The interface every configurable application implements."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Option:
    key: str
    doc: str = ""
    defaults: list[str] = field(default_factory=list)
    category: str = "Advanced"
    # bool | choice | number | color | list | text
    kind: str = "text"
    choices: list[str] = field(default_factory=list)
    # The key may be repeated; the editor falls back to a list when it is.
    multi: bool = False
    minimum: float = 0
    maximum: float = 1e9
    step: float = 1
    digits: int = 0
    # Irrelevant on this platform; still shown if the user has set it.
    hidden: bool = False


@dataclass
class Preview:
    """A live preview row shown at the top of a category page.

    render receives the app's state (see AppState in app.py) and returns Pango
    markup; it is re-run whenever one of keys changes.
    """

    category: str
    keys: frozenset[str]
    render: Callable[[object], str]


class ConfigApp:
    id = ""
    name = ""
    icon = "applications-system-symbolic"
    # [(category name, icon name)], in sidebar order
    categories: list[tuple[str, str]] = []

    def available(self) -> bool:
        raise NotImplementedError

    def config_path(self) -> Path:
        raise NotImplementedError

    def load_options(self) -> list[Option]:
        raise NotImplementedError

    def previews(self) -> list[Preview]:
        return []

    def validate(self, path: Path) -> str | None:
        """Check a candidate config file; return an error message or None."""
        return None

    def apply(self) -> str:
        """Make the running application pick up the saved config; return a status."""
        return f"Saved {self.name} config"
