"""Small, format-preserving editor for flat GTK CSS files like Waybar's style.css."""

import re
from pathlib import Path

COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}(?![\w-])")
NAMED_HEX_RE = re.compile(r"([A-Za-z][\w-]*)\s+(#[0-9a-fA-F]{6})(?![\w-])")


def _clean(selector):
    return " ".join(COMMENT_RE.sub("", selector).split())


class CssFile:
    def __init__(self, path, fallback=None):
        self.path = Path(path)
        self.fallback = Path(fallback) if fallback else None
        self.reload()

    def reload(self):
        source = self.path
        if not source.exists() and self.fallback and self.fallback.exists():
            source = self.fallback
        self.text = source.read_text() if source.exists() else ""
        self._saved = self.text

    def render(self):
        return self.text

    @property
    def dirty(self):
        return self.text != self._saved

    def mark_saved(self):
        self._saved = self.text

    # -- properties -------------------------------------------------------

    def _rule(self, selector):
        """(body start, body end) of the last rule with exactly this selector."""
        found = None
        for m in RULE_RE.finditer(self.text):
            if _clean(m.group(1)) == selector:
                found = m.span(2)
        return found

    def _declaration(self, selector, prop):
        if not (span := self._rule(selector)):
            return None
        pattern = re.compile(rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;}}]*?)\s*(;|(?=\}}))")
        found = None
        for m in pattern.finditer(self.text, *span):
            found = m
        return found

    def get(self, selector, prop):
        m = self._declaration(selector, prop)
        return m.group(1) if m else None

    def set(self, selector, prop, value):
        """Set a property; None removes it. Missing rules are added at the end."""
        if m := self._declaration(selector, prop):
            if value is None:
                start, end = m.start(), m.end()
                line = self.text.rfind("\n", 0, start) + 1
                if not self.text[line:start].strip() and self.text[end:end + 1] == "\n":
                    start, end = line, end + 1
                self.text = self.text[:start] + self.text[end:]
            else:
                self.text = self.text[:m.start(1)] + value + self.text[m.end(1):]
            return
        if value is None:
            return
        if span := self._rule(selector):
            body = self.text[span[0]:span[1]]
            if "\n" in body:
                indent = re.search(r"\n([ \t]*)\S", body)
                indent = indent.group(1) if indent else "    "
                stripped = body.rstrip(" \t")
                insert = f"{indent}{prop}: {value};\n"
                pos = span[0] + len(stripped)
                if not stripped.endswith("\n"):
                    insert = "\n" + insert
            else:
                insert, pos = f" {prop}: {value};", span[1] - len(body) + len(body.rstrip())
            self.text = self.text[:pos] + insert + self.text[pos:]
        else:
            sep = "" if not self.text or self.text.endswith("\n\n") else ("\n" if self.text.endswith("\n") else "\n\n")
            self.text += f"{sep}{selector} {{\n    {prop}: {value};\n}}\n"

    # -- colors -----------------------------------------------------------

    def colors(self):
        """[(hex, count, names, selectors)] for every #rrggbb color, in order of first use."""
        comments = [m.span() for m in COMMENT_RE.finditer(self.text)]
        names = {}
        for start, end in comments:
            for m in NAMED_HEX_RE.finditer(self.text, start, end):
                names.setdefault(m.group(2).lower(), []).append(m.group(1))
        rules = [(m.start(2), m.end(2), _clean(m.group(1))) for m in RULE_RE.finditer(self.text)]
        found = {}
        for m in HEX_RE.finditer(self.text):
            color = m.group(0).lower()
            entry = found.setdefault(color, [0, []])
            if any(s <= m.start() < e for s, e in comments):
                continue  # mentions in comments don't count as uses
            entry[0] += 1
            for start, end, selector in rules:
                if start <= m.start() < end and selector not in entry[1]:
                    entry[1].append(selector)
        return [(c, n, names.get(c, []), sels) for c, (n, sels) in found.items() if n]

    def replace_color(self, old, new):
        """Replace a color everywhere, including palette notes in comments."""
        self.text = re.sub(re.escape(old) + r"(?![\w-])", new, self.text, flags=re.I)
