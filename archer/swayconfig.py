"""Line-preserving editor for i3/Sway-style config files.

Sway configs are commands, one per line, optionally nested in blocks
(`input type:keyboard { ... }`, `mode "resize" { ... }`) and continued with a
trailing backslash. Options are addressed by their command prefix as if they
were written flat: `gaps inner`, `input type:keyboard repeat_rate`, `set $mod`.
Untouched lines, comments and alignment are kept exactly as written.
"""

import re
from pathlib import Path

CONTINUATION_RE = re.compile(r"\\\n\s*")


class Line:
    def __init__(self, text, block=(), kind="cmd"):
        self.text = text      # raw text, including indentation and continuations
        self.block = block    # enclosing block headers, e.g. ('mode "resize"',)
        self.kind = kind      # cmd | comment | blank | open | close

    @property
    def body(self):
        """The command with continuations joined and indentation stripped."""
        return CONTINUATION_RE.sub(" ", self.text).strip()

    @property
    def indent(self):
        return self.text[:len(self.text) - len(self.text.lstrip())]

    def block_tokens(self):
        return [tok for header in self.block for tok in header.split()]

    def tokens(self):
        return self.block_tokens() + self.body.split()


def parse(text):
    lines, stack, pending = [], [], None
    for raw in text.splitlines():
        pending = raw if pending is None else pending + "\n" + raw
        if pending.endswith("\\") and not pending.lstrip().startswith("#"):
            continue
        stripped = pending.strip()
        if not stripped:
            kind = "blank"
        elif stripped.startswith("#"):
            kind = "comment"
        elif stripped == "}":
            kind = "close"
            if stack:
                stack.pop()
        elif stripped.endswith("{"):
            kind = "open"
        else:
            kind = "cmd"
        lines.append(Line(pending, tuple(stack), kind))
        if kind == "open":
            stack.append(stripped[:-1].strip())
        pending = None
    if pending is not None:
        lines.append(Line(pending, tuple(stack), "cmd"))
    return lines


class SwayConfig:
    def __init__(self, path, fallback=None):
        """fallback: file to start from when path doesn't exist yet."""
        self.path = Path(path)
        self.fallback = Path(fallback) if fallback else None
        self.reload()

    def reload(self):
        source = self.path
        if not source.exists() and self.fallback and self.fallback.exists():
            source = self.fallback
        self.lines = parse(source.read_text() if source.exists() else "")
        self._saved = self.render()

    def render(self):
        return "\n".join(line.text for line in self.lines) + "\n" if self.lines else ""

    @property
    def dirty(self):
        return self.render() != self._saved

    def mark_saved(self):
        self._saved = self.render()

    def outputs(self):
        """[(path, new contents)] for every file that needs writing."""
        return [(self.path, self.render())]

    # -- option access by command prefix ---------------------------------

    def _matches(self, key_tokens):
        n = len(key_tokens)
        return [line for line in self.lines
                if line.kind == "cmd" and line.tokens()[:n] == key_tokens
                and len(line.tokens()) > n]

    def get(self, key):
        key_tokens = key.split()
        values = [" ".join(line.tokens()[len(key_tokens):]) for line in self._matches(key_tokens)]
        return values or None

    def set(self, key, values):
        key_tokens = key.split()
        matches = self._matches(key_tokens)
        values = values or []
        for line, value in zip(matches, values):
            local = key_tokens[len(line.block_tokens()):]
            # Keep the original spacing between key and value (column alignment).
            m = re.match(r"\s*" + r"\s+".join(map(re.escape, local)) + r"\s+", line.text)
            line.text = m.group(0) + value if m else f"{line.indent}{' '.join(local)} {value}"
        for line in matches[len(values):]:
            self.lines.remove(line)
        extra = values[len(matches):]
        if not extra:
            return
        anchor = self._anchor(key_tokens)
        if anchor is None:
            for value in extra:
                self.lines.append(Line(f"{key} {value}"))
            return
        local = key_tokens[len(anchor.block_tokens()):]
        index = self.lines.index(anchor) + 1
        for value in extra:
            self.lines.insert(index, Line(f"{anchor.indent}{' '.join(local)} {value}", anchor.block))
            index += 1

    def _anchor(self, key_tokens):
        """The line a new `key value` belongs after: the last command sharing
        the longest prefix with key, in a block that key can live in."""
        best, best_len = None, 0
        for line in self.lines:
            if line.kind != "cmd":
                continue
            block = line.block_tokens()
            if block != key_tokens[:len(block)] or len(block) >= len(key_tokens):
                continue
            tokens = line.tokens()
            common = 0
            while common < min(len(tokens), len(key_tokens)) and tokens[common] == key_tokens[common]:
                common += 1
            if common >= 1 and common >= best_len:
                best, best_len = line, common
        return best

    # -- whole-line access, for list editors like keybindings -------------

    def commands(self, directives, block=()):
        return [line for line in self.lines
                if line.kind == "cmd" and line.block == block
                and line.body.split(None, 1)[0] in directives]

    def blocks(self, kind):
        """Top-level block headers of a kind, e.g. blocks("mode")."""
        return [line.block for line in self.lines
                if line.kind != "open" and len(line.block) == 1
                and line.block[0].split()[0] == kind
                and line is self._first_in(line.block)]

    def _first_in(self, block):
        return next(line for line in self.lines if line.block == block)

    def replace_line(self, line, body):
        line.text = line.indent + body

    def remove_line(self, line):
        self.lines.remove(line)

    def add_line(self, body, after=None, block=()):
        if after is not None:
            line = Line(after.indent + body, after.block)
            self.lines.insert(self.lines.index(after) + 1, line)
            return line
        if block:
            # Insert before the block's closing brace.
            inside = [i for i, l in enumerate(self.lines) if l.block[:len(block)] == block]
            close = inside[-1] + 1 if inside else len(self.lines)
            line = Line("    " * len(block) + body, block)
            self.lines.insert(close, line)
            return line
        line = Line(body)
        self.lines.append(line)
        return line
