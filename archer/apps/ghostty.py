"""Ghostty terminal emulator.

The option list, documentation and defaults all come from the installed
`ghostty` binary, so they always match the version on this machine.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from gi.repository import GLib

from ..configfile import parse_text
from .base import ConfigApp, Option, Preview

OPTION_RE = re.compile(r"^([a-z0-9-]+) = ?(.*)$")
NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")
HEX_RE = re.compile(r"#?[0-9a-fA-F]{6}")
COLOR_KEY_RE = re.compile(r"(^|-)(color|background|foreground|fill|text)$")

FONT_FAMILY_KEYS = {"font-family", "font-family-bold", "font-family-italic", "font-family-bold-italic"}
REPEATABLE = {
    "font-feature", "font-variation", "font-variation-bold", "font-variation-italic",
    "font-variation-bold-italic", "font-codepoint-map", "clipboard-codepoint-map", "input",
    "key-remap", "config-file", "custom-shader", "gtk-custom-css", "env", "link", "keybind",
    "palette", "command-palette-entry",
}
# Options whose documented bullet lists are examples, not the full set of values.
NOT_ENUMS = {"command", "working-directory", "quit-after-last-window-closed-delay", "cursor-color"}
CHOICE_OVERRIDES = {"shell-integration": ["none", "detect", "bash", "elvish", "fish", "zsh"]}
NUMBER_SPECS = {
    "font-size": dict(minimum=1, maximum=200, step=0.5, digits=1),
}

CATEGORY_RULES = [
    ("Keybindings", ("keybind", "key-remap")),
    ("Fonts", ("font-", "adjust-", "freetype-", "grapheme-width-method")),
    ("Cursor", ("cursor-",)),
    ("Colors", ("theme", "background", "foreground", "palette", "selection-", "minimum-contrast",
                "bold-color", "alpha-blending", "faint-opacity", "unfocused-split", "split-divider",
                "search-")),
    ("Quick Terminal", ("quick-terminal", "gtk-quick-terminal")),
    ("Window", ("window-", "resize-overlay", "gtk-", "maximize", "fullscreen", "initial-window",
                "focus-follows-mouse", "title", "app-notifications", "scrollbar", "confirm-close")),
    ("Mouse & Clipboard", ("mouse-", "clipboard-", "copy-on-select", "click-repeat", "right-click",
                           "link")),
    ("Shell", ("command", "initial-command", "shell-integration", "working-directory", "env",
               "input", "wait-after-command", "abnormal-command", "term", "scrollback",
               "notify-on-command")),
]

DEFAULT_FONT = "JetBrains Mono"  # bundled with Ghostty and used when font-family is unset


def _ghostty(*args):
    return subprocess.run(["ghostty", *args], capture_output=True, text=True, check=True).stdout


def _category(key):
    for name, prefixes in CATEGORY_RULES:
        if key.startswith(prefixes):
            return name
    return "Advanced"


def _hex(value):
    if value and HEX_RE.fullmatch(value):
        return value if value.startswith("#") else "#" + value
    return None


class Ghostty(ConfigApp):
    id = "ghostty"
    name = "Ghostty"
    icon = "utilities-terminal-symbolic"
    categories = [
        ("Fonts", "font-x-generic-symbolic"),
        ("Colors", "preferences-desktop-appearance-symbolic"),
        ("Cursor", "insert-text-symbolic"),
        ("Window", "window-maximize-symbolic"),
        ("Mouse & Clipboard", "input-mouse-symbolic"),
        ("Shell", "utilities-terminal-symbolic"),
        ("Keybindings", "input-keyboard-symbolic"),
        ("Quick Terminal", "go-down-symbolic"),
        ("Advanced", "emblem-system-symbolic"),
    ]

    def available(self):
        return shutil.which("ghostty") is not None

    def config_dir(self):
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ghostty"

    def config_path(self):
        modern = self.config_dir() / "config.ghostty"
        return modern if modern.exists() else self.config_dir() / "config"

    def load_options(self, cfg):
        fonts = sorted({line for line in _ghostty("+list-fonts").splitlines()
                        if line and not line.startswith(" ")}, key=str.lower)
        themes = [re.sub(r" \((resources|user)\)$", "", line)
                  for line in _ghostty("+list-themes", "--plain").splitlines() if line]

        options, doc = {}, []
        for line in _ghostty("+show-config", "--default", "--docs").splitlines():
            if line.startswith("#"):
                doc.append(line[2:])
                continue
            m = OPTION_RE.match(line)
            if not m:
                continue
            key, value = m.groups()
            if key in options:
                options[key].defaults.append(value)
            else:
                options[key] = Option(key=key, doc="\n".join(doc).strip(), defaults=[value])
            doc = []

        for opt in options.values():
            self._classify(opt, fonts, themes)
        return list(options.values())

    def _classify(self, opt, fonts, themes):
        key, default = opt.key, opt.defaults[0]
        opt.category = _category(key)
        opt.hidden = key.startswith("macos-") or "macOS only" in opt.doc

        enum = list(dict.fromkeys(re.findall(r"^\s*\*\s+`([^`\s]+)`", opt.doc, re.M)))
        is_enum = (key not in NOT_ENUMS and len(enum) >= 2
                   and all(re.fullmatch(r"[\w.-]+", v) for v in enum)
                   and (default == "" or default in enum))

        if key == "theme":
            opt.kind, opt.choices = "choice", themes
        elif key in FONT_FAMILY_KEYS:
            opt.kind, opt.choices, opt.multi = "choice", fonts, True
        elif len(opt.defaults) > 1 or key in REPEATABLE:
            opt.kind = "list"
        elif key in CHOICE_OVERRIDES:
            opt.kind, opt.choices = "choice", CHOICE_OVERRIDES[key]
        elif default in ("true", "false"):
            extra = [v for v in enum if v not in ("true", "false") and re.fullmatch(r"[\w.-]+", v)]
            if extra:  # e.g. copy-on-select = true | false | clipboard
                opt.kind, opt.choices = "choice", ["true", "false", *extra]
            else:
                opt.kind = "bool"
        elif is_enum:
            opt.kind, opt.choices = "choice", enum
        elif NUMBER_RE.fullmatch(default):
            opt.kind = "number"
            if key in NUMBER_SPECS:
                for attr, value in NUMBER_SPECS[key].items():
                    setattr(opt, attr, value)
            elif "opacity" in key:
                opt.minimum, opt.maximum, opt.step, opt.digits = 0, 1, 0.05, 2
            elif "." in default:
                opt.maximum, opt.step, opt.digits = 1e6, 0.1, 2
            elif default.startswith("-"):
                opt.minimum = -1e9
        elif _hex(default) or COLOR_KEY_RE.search(key):
            opt.kind = "color"

    def previews(self):
        return [
            Preview("Fonts", frozenset({"font-family", "font-size"}), self._font_preview),
            Preview("Colors", frozenset({"theme", "background", "foreground", "palette",
                                         "font-family"}), self._color_preview),
        ]

    def validate(self, path, target):
        result = subprocess.run(["ghostty", "+validate-config", f"--config-file={path}"],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return None
        output = (result.stdout + result.stderr).replace(f"{path}:", "line ").strip()
        return output or "Ghostty rejected the config."

    def apply(self):
        # Ghostty reloads its config on SIGUSR2 (the same as ctrl+shift+,).
        running = subprocess.run(["pkill", "-USR2", "-x", "ghostty"]).returncode == 0
        return "Saved and reloaded Ghostty" if running else "Saved Ghostty config"

    # -- previews ---------------------------------------------------------

    def _font_family(self, state):
        return next((v for v in state.user("font-family") or [] if v), DEFAULT_FONT)

    def _font_preview(self, state):
        family = GLib.markup_escape_text(self._font_family(state))
        size = state.first("font-size") or "13"
        sample = GLib.markup_escape_text(
            "The quick brown fox jumps over the lazy dog\n"
            "0123456789  -> => != === <= >= {} [] () ~/.config/ghostty $ ")
        return f'<span font_desc="{family}, {size}">{sample}</span>'

    def _theme_colors(self, theme):
        # "light:Foo,dark:Bar" picks per system theme; preview the dark one.
        parts = [p.strip() for p in theme.split(",")]
        name = next((p[5:] for p in parts if p.startswith("dark:")), parts[0])
        name = name.split(":", 1)[1] if name.startswith("light:") else name
        dirs = [self.config_dir() / "themes", Path("/usr/share/ghostty/themes")]
        if res := os.environ.get("GHOSTTY_RESOURCES_DIR"):
            dirs.insert(1, Path(res) / "themes")
        for d in dirs:
            if (path := d / name).is_file():
                return parse_text(path.read_text())
        return {}

    def _color_preview(self, state):
        palette = {}
        colors = {"background": "#282c34", "foreground": "#ffffff"}
        layers = [{"palette": state.defaults("palette")}]
        if theme := state.first("theme"):
            layers.append(self._theme_colors(theme))
        layers.append({k: v for k in ("background", "foreground", "palette") if (v := state.user(k))})
        for layer in layers:
            for entry in layer.get("palette", []):
                index, _, color = entry.partition("=")
                if index.strip().isdigit() and (c := _hex(color.strip())):
                    palette[int(index)] = c
            for k in ("background", "foreground"):
                if (c := _hex((layer.get(k) or [""])[0])):
                    colors[k] = c

        bg, fg = colors["background"], colors["foreground"]
        p = [palette.get(i, fg) for i in range(16)]
        width = 44

        def line(*segments):
            text = "".join(t for t, _ in segments)
            body = "".join(f'<span foreground="{c}">{GLib.markup_escape_text(t)}</span>'
                           for t, c in segments)
            return body + " " * max(0, width - len(text))

        def swatches(start):
            return " " + "".join(f'<span background="{p[i]}">     </span>'
                                 for i in range(start, start + 8)) + "   "

        rows = [
            line((" ", fg), ("lhansen", p[2]), (" in ", fg), ("~/dev", p[4]), (" $ ls", fg)),
            line((" README.md  ", fg), ("src/  ", p[12]), ("build.sh  ", p[10]), ("notes.txt", fg)),
            line((" error:", p[1]), (" missing file  ", fg), ("warning:", p[3]), (" retrying", fg)),
            " " * width,
            swatches(0),
            swatches(8),
            " " * width,
        ]
        family = GLib.markup_escape_text(self._font_family(state))
        return (f'<span font_desc="{family}, 11" background="{bg}" foreground="{fg}">'
                + "\n".join(rows) + "</span>")
