"""Line-preserving editor for `key = value` config files.

Only lines for keys that are explicitly changed get rewritten, so comments,
blank lines and ordering in the user's file survive every save.
"""

import re
from pathlib import Path

LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=\s?(.*?)\s*$")


def unquote(value):
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def quote(value):
    if value == "" or value != value.strip():
        return f'"{value}"'
    return value


def parse_line(line):
    """Return (key, value) for an assignment line, or None for comments/blanks."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    m = LINE_RE.match(line)
    return (m.group(1), unquote(m.group(2))) if m else None


def parse_text(text):
    """Return {key: [values]} for every assignment in text."""
    values = {}
    for line in text.splitlines():
        if parsed := parse_line(line):
            values.setdefault(parsed[0], []).append(parsed[1])
    return values


class KeyValueConfig:
    def __init__(self, path):
        self.path = Path(path)
        self.reload()

    def reload(self):
        text = self.path.read_text() if self.path.exists() else ""
        self.lines = text.splitlines()
        self._saved = self.render()

    def render(self):
        return "\n".join(self.lines) + "\n" if self.lines else ""

    @property
    def dirty(self):
        return self.render() != self._saved

    def mark_saved(self):
        self._saved = self.render()

    def outputs(self):
        """[(path, new contents)] for every file that needs writing."""
        return [(self.path, self.render())]

    def keys(self):
        return list(parse_text("\n".join(self.lines)))

    def get(self, key):
        """The user's values for key, or None if the file doesn't set it."""
        values = [p[1] for line in self.lines if (p := parse_line(line)) and p[0] == key]
        return values or None

    def set(self, key, values):
        """Replace every line for key with values; None or [] removes the key."""
        indices = [i for i, line in enumerate(self.lines) if (p := parse_line(line)) and p[0] == key]
        new_lines = [f"{key} = {quote(v)}" for v in values or []]
        if indices:
            for i in reversed(indices):
                del self.lines[i]
            self.lines[indices[0]:indices[0]] = new_lines
        else:
            self.lines.extend(new_lines)
