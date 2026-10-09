"""Comment- and format-preserving editor for JSON-with-comments files.

The parser records where every value sits in the text, so edits splice new
text into exactly that span and leave comments, spacing and key order alone.
Options are addressed by key path, e.g. ("clock", "format").
"""

import json
import re
from json.decoder import scanstring
from pathlib import Path

NUMBER_RE = re.compile(r"-?\d+(\.\d+)?([eE][+-]?\d+)?")


class JsoncError(ValueError):
    def __init__(self, message, text, pos):
        super().__init__(f"line {text.count(chr(10), 0, pos) + 1}: {message}")


class Node:
    def __init__(self, kind, start, end, value=None):
        self.kind = kind      # object | array | string | number | literal
        self.start, self.end = start, end
        self.value = value    # the Python value
        self.members = []     # objects: [Member]
        self.items = []       # arrays: [Node]


class Member:
    def __init__(self, key, start, value):
        self.key, self.start, self.value = key, start, value


class _Parser:
    def __init__(self, text):
        self.t, self.i = text, 0

    def error(self, message, pos=None):
        return JsoncError(message, self.t, self.i if pos is None else pos)

    def skip(self):
        t = self.t
        while self.i < len(t):
            if t[self.i] in " \t\r\n":
                self.i += 1
            elif t.startswith("//", self.i):
                end = t.find("\n", self.i)
                self.i = len(t) if end < 0 else end + 1
            elif t.startswith("/*", self.i):
                end = t.find("*/", self.i + 2)
                if end < 0:
                    raise self.error("unterminated comment")
                self.i = end + 2
            else:
                break

    def peek(self):
        self.skip()
        return self.t[self.i:self.i + 1]

    def string(self):
        start = self.i
        try:
            value, self.i = scanstring(self.t, start + 1)
        except ValueError as e:
            raise self.error(f"invalid string ({e.msg})", start) from None
        return Node("string", start, self.i, value)

    def value(self):
        c = self.peek()
        start = self.i
        if c == "{":
            return self.object()
        if c == "[":
            return self.array()
        if c == '"':
            return self.string()
        if m := NUMBER_RE.match(self.t, start):
            self.i = m.end()
            return Node("number", start, self.i, json.loads(m.group(0)))
        for word, value in (("true", True), ("false", False), ("null", None)):
            if self.t.startswith(word, start):
                self.i += len(word)
                return Node("literal", start, self.i, value)
        raise self.error(f"unexpected {c!r}" if c else "unexpected end of file")

    def object(self):
        node = Node("object", self.i, None)
        self.i += 1
        while True:
            c = self.peek()
            if c == "}":
                break
            if c != '"':
                raise self.error("expected a quoted key or }")
            key = self.string()
            if self.peek() != ":":
                raise self.error("expected :")
            self.i += 1
            node.members.append(Member(key.value, key.start, self.value()))
            c = self.peek()
            if c == ",":
                self.i += 1
            elif c != "}":
                raise self.error("expected , or }")
        self.i += 1
        node.end = self.i
        node.value = {m.key: m.value.value for m in node.members}
        return node

    def array(self):
        node = Node("array", self.i, None)
        self.i += 1
        while True:
            if self.peek() == "]":
                break
            node.items.append(self.value())
            c = self.peek()
            if c == ",":
                self.i += 1
            elif c != "]":
                raise self.error("expected , or ]")
        self.i += 1
        node.end = self.i
        node.value = [item.value for item in node.items]
        return node


def parse(text):
    parser = _Parser(text)
    node = parser.value()
    if parser.peek():
        raise parser.error("unexpected content after the end")
    return node


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


class JsoncFile:
    def __init__(self, path, fallback=None):
        self.path = Path(path)
        self.fallback = Path(fallback) if fallback else None
        self.reload()

    def reload(self):
        source = self.path
        if not source.exists() and self.fallback and self.fallback.exists():
            source = self.fallback
        self.text = source.read_text() if source.exists() else "{\n}\n"
        self._saved = self.text

    def render(self):
        return self.text

    @property
    def dirty(self):
        return self.text != self._saved

    def mark_saved(self):
        self._saved = self.text

    def root(self):
        """The top-level object. A file holding an array of bars edits the first bar."""
        node = parse(self.text)
        if node.kind == "array":
            node = next((item for item in node.items if item.kind == "object"), None)
        if node is None or node.kind != "object":
            raise JsoncError("expected an object at the top level", self.text, 0)
        return node

    @staticmethod
    def member(obj, key):
        return next((m for m in reversed(obj.members) if m.key == key), None)

    def find(self, path):
        node = self.root()
        for key in path:
            if node.kind != "object" or not (m := self.member(node, key)):
                return None
            node = m.value
        return node

    def replace(self, node, text):
        self.text = self.text[:node.start] + text + self.text[node.end:]

    def _line_start(self, pos):
        return self.text.rfind("\n", 0, pos) + 1

    def _indent(self, pos):
        start = self._line_start(pos)
        return self.text[start:len(self.text) - len(self.text[start:].lstrip(" \t"))]

    def remove(self, path):
        parent = self.find(path[:-1])
        if parent is None or parent.kind != "object" or not (m := self.member(parent, path[-1])):
            return
        t, index = self.text, parent.members.index(m)
        start, end = m.start, m.value.end
        if index < len(parent.members) - 1:
            # Not last: take the following comma, and the whole line if that leaves it empty.
            comma = re.compile(r"[ \t]*,[ \t]*").match(t, end)
            end = comma.end() if comma else end
            if t[end:end + 1] == "\n" and not t[self._line_start(start):start].strip():
                start, end = self._line_start(start), end + 1
        elif index > 0:
            # Last: take the comma after the previous member instead.
            comma = t.rfind(",", parent.members[index - 1].value.end, start)
            if comma >= 0:
                start = comma
        self.text = t[:start] + t[end:]

    def insert(self, path, value_text):
        parent = self.find(path[:-1])
        if parent is None:
            self.insert(path[:-1], "{}")
            parent = self.find(path[:-1])
        if parent.kind != "object":
            raise JsoncError(f"{' › '.join(path[:-1])} is not an object", self.text, parent.start)
        entry = f"{json.dumps(path[-1], ensure_ascii=False)}: {value_text}"
        if parent.members:
            last = parent.members[-1]
            if "\n" in self.text[parent.start:parent.end]:
                text = f",\n{self._indent(last.start)}{entry}"
            else:
                text = f", {entry}"
            pos = last.value.end
        else:
            base = self._indent(parent.start)
            unit = self._indent_unit()
            inner = self.text[parent.start + 1:parent.end - 1]
            if inner.strip():  # only comments inside; keep it simple and append
                text = f"\n{base}{unit}{entry}\n{base}"
                pos = parent.end - 1
            else:
                text = f"{{\n{base}{unit}{entry}\n{base}}}"
                self.text = self.text[:parent.start] + text + self.text[parent.end:]
                return
        self.text = self.text[:pos] + text + self.text[pos:]

    def _indent_unit(self):
        root = self.root()
        if root.members:
            indent = self._indent(root.members[0].start)
            if indent:
                return indent
        return "  "
