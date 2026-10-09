"""Waybar status bar.

Two files: `config` (JSON with comments) for the bar and its modules, and
`style.css` for the look. Bar options are curated from waybar(5); module
pages are built from what's in your config plus each module's common
options. Style options and the color palette edit style.css in place.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from gi.repository import Adw, Gdk, Gtk

from ..cssfile import CssFile
from ..jsonc import JsoncError, JsoncFile, encode, parse
from ..rows import OptionRow
from .base import ConfigApp, Option

SEP = " › "
STYLE = "style.css"
NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")
MODULE_LISTS = ["modules-left", "modules-center", "modules-right"]


def opt(key, kind, doc, default="", choices=(), **kw):
    return Option(key=key, doc=doc, defaults=[default], kind=kind, choices=list(choices), **kw)


BAR = [
    opt("position", "choice", "Screen edge the bar sits on.", "top",
        ["top", "bottom", "left", "right"]),
    opt("layer", "choice", "Whether the bar is drawn above or below windows.", "bottom",
        ["top", "bottom", "overlay"]),
    opt("height", "number", "Height in pixels. Leave unset to fit the content.", maximum=500),
    opt("width", "number", "Width in pixels. Leave unset to span the output.", maximum=10000),
    opt("spacing", "number", "Space in pixels between modules.", "0", maximum=200),
    opt("margin", "text", "Space around the bar, CSS-style: `top right bottom left`."),
    opt("margin-top", "number", "Space above the bar in pixels.", minimum=-500, maximum=500),
    opt("margin-bottom", "number", "Space below the bar in pixels.", minimum=-500, maximum=500),
    opt("margin-left", "number", "Space left of the bar in pixels.", minimum=-500, maximum=500),
    opt("margin-right", "number", "Space right of the bar in pixels.", minimum=-500, maximum=500),
    opt("mode", "choice", "`dock` reserves space; `overlay` floats over windows; `hide` shows "
        "the bar only while the modifier is held; `invisible` hides it.", "dock",
        ["dock", "hide", "invisible", "overlay"]),
    opt("exclusive", "bool", "Reserve screen space so windows don't go under the bar.", "true"),
    opt("passthrough", "bool", "Let clicks pass through the bar to windows below.", "false"),
    opt("fixed-center", "bool", "Keep the center modules centered on the screen, not between "
        "the left and right modules.", "true"),
    opt("reload_style_on_change", "bool", "Reload style.css automatically whenever it changes.",
        "false"),
    opt("start_hidden", "bool", "Start with the bar hidden.", "false"),
    opt("output", "text", "Only show the bar on this output, e.g. `DP-1`."),
    opt("name", "text", "Name for the bar, used as a CSS class (`window#waybar.<name>`)."),
]

LAYOUT_DOCS = {
    "modules-left": "Modules on the left side, one per line, in order.",
    "modules-center": "Modules in the center, one per line, in order.",
    "modules-right": "Modules on the right side, one per line, in order.",
}

# Keys most modules understand: key -> (kind, doc, default)
COMMON = {
    "format": ("text", "What the module shows. `{}` placeholders depend on the module.", ""),
    "format-alt": ("text", "Alternative format, toggled by clicking the module.", ""),
    "tooltip": ("bool", "Show a tooltip on hover.", "true"),
    "tooltip-format": ("text", "Tooltip text; uses the same placeholders as format.", ""),
    "interval": ("number", "Seconds between updates.", ""),
    "max-length": ("number", "Truncate the text to this many characters.", ""),
    "min-length": ("number", "Pad the text to at least this many characters.", ""),
    "on-click": ("text", "Command to run on left click.", ""),
    "on-click-middle": ("text", "Command to run on middle click.", ""),
    "on-click-right": ("text", "Command to run on right click.", ""),
    "on-scroll-up": ("text", "Command to run when scrolling up.", ""),
    "on-scroll-down": ("text", "Command to run when scrolling down.", ""),
}
EXTRA = {
    "battery": {
        "format-charging": ("text", "Format while charging.", ""),
        "format-plugged": ("text", "Format while plugged in but not charging.", ""),
        "format-full": ("text", "Format when fully charged.", ""),
        "states › warning": ("number", "Capacity (%) at or below which the `warning` CSS class "
                             "applies.", ""),
        "states › critical": ("number", "Capacity (%) at or below which the `critical` CSS class "
                              "applies.", ""),
        "full-at": ("number", "Treat this capacity (%) as full.", ""),
        "bat": ("text", "Battery to show, e.g. `BAT0`. Defaults to the first one.", ""),
    },
    "cpu": {
        "states › warning": ("number", "Usage (%) at or above which `warning` applies.", ""),
        "states › critical": ("number", "Usage (%) at or above which `critical` applies.", ""),
    },
    "memory": {
        "states › warning": ("number", "Usage (%) at or above which `warning` applies.", ""),
        "states › critical": ("number", "Usage (%) at or above which `critical` applies.", ""),
    },
    "clock": {
        "timezone": ("text", "Time zone, e.g. `America/Los_Angeles`. Defaults to local time.", ""),
        "locale": ("text", "Locale for names of days and months, e.g. `en_US.UTF-8`.", ""),
    },
    "pulseaudio": {
        "format-muted": ("text", "Format while muted.", ""),
        "format-bluetooth": ("text", "Format when the output is a Bluetooth device.", ""),
        "scroll-step": ("number", "Volume change (%) per scroll step.", "1"),
        "max-volume": ("number", "Highest volume (%) scrolling can reach.", "100"),
    },
    "network": {
        "format-wifi": ("text", "Format on Wi-Fi.", ""),
        "format-ethernet": ("text", "Format on a wired connection.", ""),
        "format-disconnected": ("text", "Format when disconnected.", ""),
        "interface": ("text", "Interface to show, e.g. `wlan0`.", ""),
    },
    "sway/workspaces": {
        "all-outputs": ("bool", "Show workspaces from every output, not just this one.", "false"),
        "disable-scroll": ("bool", "Don't switch workspaces by scrolling over the bar.", "false"),
    },
}
NO_INTERVAL = ("sway/", "hyprland/", "niri/", "tray", "custom/")

MODULE_ICONS = {
    "clock": "preferences-system-time-symbolic",
    "battery": "battery-good-symbolic",
    "cpu": "utilities-system-monitor-symbolic",
    "memory": "drive-harddisk-symbolic",
    "pulseaudio": "audio-volume-high-symbolic",
    "wireplumber": "audio-volume-high-symbolic",
    "network": "network-wireless-symbolic",
    "bluetooth": "bluetooth-symbolic",
    "backlight": "display-brightness-symbolic",
    "tray": "view-app-grid-symbolic",
}

STYLE_OPTIONS = [
    ("*", "font-family", "choice", "Font for the whole bar."),
    ("*", "font-size", "text", "Text size, e.g. `13px`."),
    ("*", "font-weight", "choice", "`normal` or `bold`."),
    ("window#waybar", "background", "color", "Bar background color."),
    ("window#waybar", "color", "color", "Default text color."),
    ("window#waybar", "opacity", "text", "Opacity of the whole bar, from 0 to 1."),
]


def style_key(selector, prop):
    return f"{STYLE}{SEP}{selector}{SEP}{prop}"


def _type_of(node):
    if node.kind == "string":
        return "string"
    if node.kind == "number":
        return "number"
    if node.kind == "literal" and isinstance(node.value, bool):
        return "bool"
    if node.kind == "array" and node.items and all(i.kind == "string" for i in node.items):
        return "array"
    return "json"


class WaybarConfig:
    """Both Waybar files behind the get/set interface the option rows use."""

    def __init__(self, config_path, style_path):
        self.json = JsoncFile(config_path, fallback="/etc/xdg/waybar/config.jsonc")
        self.css = CssFile(style_path, fallback="/etc/xdg/waybar/style.css")
        self.path = self.json.path
        self.types = {}  # key -> string | number | bool | array | json, for new keys

    def files(self):
        return [self.json, self.css]

    def reload(self):
        for f in self.files():
            f.reload()

    @property
    def dirty(self):
        return any(f.dirty for f in self.files())

    def mark_saved(self):
        for f in self.files():
            f.mark_saved()

    def outputs(self):
        return [(f.path, f.render()) for f in self.files() if f.dirty]

    def _node(self, key):
        try:
            return self.json.find(key.split(SEP))
        except JsoncError:
            return None

    def get(self, key):
        if key.startswith(STYLE + SEP):
            selector, prop = key.split(SEP)[1:]
            value = self.css.get(selector, prop)
            if value is not None and prop == "font-family":
                value = value.strip("\"'")
            return None if value is None else [value]
        node = self._node(key)
        if node is None or (node.kind == "literal" and node.value is None):
            return None
        kind = _type_of(node)
        if kind == "string":
            return [node.value]
        if kind == "bool":
            return ["true" if node.value else "false"]
        if kind == "array":
            return list(node.value)
        if kind == "json":
            return [encode(node.value)]
        return [self.json.text[node.start:node.end]]

    def set(self, key, values):
        if key.startswith(STYLE + SEP):
            selector, prop = key.split(SEP)[1:]
            value = values[0] if values else None
            if value is not None and prop == "font-family" and not value.startswith(("'", '"')):
                value = f'"{value}"'
            self.css.set(selector, prop, value)
            return
        path = key.split(SEP)
        node = self._node(key)
        if not values:
            self.json.remove(path)
            return
        # An existing value keeps its JSON type; new keys use the option's type.
        kind = _type_of(node) if node is not None else self.types.get(key, "string")
        text = self._encode(kind, values)
        if node is not None:
            self.json.replace(node, text)
        else:
            self.json.insert(path, text)

    @staticmethod
    def _encode(kind, values):
        value = values[0]
        if kind == "array":
            return encode(values)
        if kind == "number" and NUMBER_RE.fullmatch(value):
            return value
        if kind == "bool" and value in ("true", "false"):
            return value
        if kind == "json":
            try:
                parse(value)
                return value
            except JsoncError:
                pass
        return encode(value)


class Waybar(ConfigApp):
    id = "waybar"
    name = "Waybar"
    icon = "view-continuous-symbolic"
    base_categories = [
        ("Bar", "view-continuous-symbolic"),
        ("Layout", "view-columns-symbolic"),
        ("Style", "preferences-desktop-appearance-symbolic"),
    ]
    categories = base_categories

    def available(self):
        return shutil.which("waybar") is not None

    def config_dir(self):
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "waybar"

    def config_path(self):
        jsonc = self.config_dir() / "config.jsonc"
        return jsonc if jsonc.exists() else self.config_dir() / "config"

    def open_config(self, path):
        return WaybarConfig(path, self.config_dir() / "style.css")

    def load_options(self, cfg):
        options = []
        for o in BAR:
            o.category = "Bar"
            options.append(o)
        for key, doc in LAYOUT_DOCS.items():
            options.append(Option(key=key, doc=doc, defaults=[""], kind="list", category="Layout"))

        fonts = sorted({f.strip() for line in subprocess.run(
            ["fc-list", ":", "family"], capture_output=True, text=True).stdout.splitlines()
            for f in line.split(",") if f.strip()}, key=str.lower)
        for selector, prop, kind, doc in STYLE_OPTIONS:
            choices = fonts if prop == "font-family" else ["normal", "bold"] if prop == "font-weight" else []
            options.append(Option(key=style_key(selector, prop), doc=doc, defaults=[""], kind=kind,
                                  choices=choices, category="Style", label=f"{selector} {{ {prop} }}"))

        modules = self._modules(cfg)
        for module in modules:
            options += self._module_options(cfg, module)
        self.categories = self.base_categories + [
            (m, MODULE_ICONS.get(m.split("#")[0], "application-x-addon-symbolic")) for m in modules]

        types = {"bool": "bool", "number": "number", "list": "array"}
        for o in options:
            cfg.types[o.key] = types.get(o.kind, "string")
        return options

    def _modules(self, cfg):
        try:
            root = cfg.json.root()
        except JsoncError:
            return []
        modules = []
        for key in MODULE_LISTS:
            for name in root.value.get(key) or []:
                if isinstance(name, str) and name not in modules:
                    modules.append(name)
        for m in root.members:
            if m.value.kind == "object" and m.key not in modules:
                modules.append(m.key)
        return modules

    def _module_options(self, cfg, module):
        specs = dict(COMMON)
        if module.startswith(NO_INTERVAL):
            specs.pop("interval")
        specs.update(EXTRA.get(module.split("#")[0], {}))

        # Everything already in the config, flattened to leaf keys.
        node = cfg._node(module)
        if node is not None and node.kind == "object":
            def walk(obj, prefix):
                for m in obj.members:
                    path = prefix + [m.key]
                    if m.value.kind == "object" and m.value.members:
                        walk(m.value, path)
                    else:
                        local = SEP.join(path)
                        if local not in specs:
                            kind = {"bool": "bool", "number": "number", "array": "list"}.get(
                                _type_of(m.value), "text")
                            specs[local] = (kind, "", "")
            walk(node, [])

        options = []
        for local, (kind, doc, default) in specs.items():
            key = f"{module}{SEP}{local}"
            if not doc:
                doc = f"`{local}` for the {module} module. See waybar-{module.split('/')[-1].split('#')[0]}(5)."
            o = Option(key=key, doc=doc, defaults=[default], kind=kind, category=module, label=local)
            if kind == "number":
                o.maximum = 1e6
            options.append(o)
        return options

    def build_page(self, category, state, on_change):
        if category != "Style":
            return None
        rows = []

        def changed(_key=None):
            on_change()

        def palette_changed():
            for row in rows:
                row.refresh()
            on_change()

        font_group = Adw.PreferencesGroup(title="Bar style", description=str(state.cfg.css.path))
        for o in state.options:
            if o.category == "Style":
                row = OptionRow(o, state, changed, o.label)
                font_group.add(row.widget)
                rows.append(row)

        palette = Adw.PreferencesGroup(
            title="Palette", description="Every color in style.css. Changing one replaces it "
                                         "everywhere it's used.")
        for color, count, names, selectors in state.cfg.css.colors():
            palette.add(self._palette_row(state.cfg.css, color, count, names, selectors,
                                          palette_changed))
        return [font_group, palette]

    def _palette_row(self, css, color, count, names, selectors, on_change):
        current = [color]

        def title():
            label = " / ".join(dict.fromkeys(names))
            return f"{label}  {current[0]}" if label else current[0]

        shown = ", ".join(selectors[:3]) + (f" +{len(selectors) - 3} more" if len(selectors) > 3 else "")
        row = Adw.ActionRow(title=title(), subtitle=f"Used {count}× in {shown}", use_markup=False)
        rgba = Gdk.RGBA()
        rgba.parse(color)
        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), rgba=rgba,
                                       valign=Gtk.Align.CENTER)

        def on_rgba(btn, _):
            c = btn.get_rgba()
            new = "#" + "".join(f"{round(v * 255):02x}" for v in (c.red, c.green, c.blue))
            if new != current[0]:
                css.replace_color(current[0], new)
                current[0] = new
                row.set_title(title())
                on_change()

        button.connect("notify::rgba", on_rgba)
        row.add_suffix(button)
        return row

    def validate(self, path, target):
        text = path.read_text()
        if target.suffix == ".css":
            errors = []
            provider = Gtk.CssProvider()

            def on_error(_provider, section, error):
                if error.matches(Gtk.css_parser_error_quark(), Gtk.CssParserError.SYNTAX):
                    line = section.get_start_location().lines + 1
                    errors.append(f"line {line}: {error.message}")

            provider.connect("parsing-error", on_error)
            provider.load_from_string(text)
            return "\n".join(errors) or None
        try:
            parse(text)
        except JsoncError as e:
            return str(e)
        return None

    def apply(self):
        # Waybar reloads its config and style on SIGUSR2.
        if subprocess.run(["pkill", "-USR2", "-x", "waybar"]).returncode == 0:
            return "Saved and reloaded Waybar"
        return "Saved Waybar config"
