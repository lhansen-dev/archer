"""Sway window manager.

Sway has no machine-readable option list, so the settings here are curated
from sway(5), sway-input(5) and sway-output(5). Displays and input devices
come from the running session via swaymsg. Keybindings, window rules,
startup commands and variables are edited line by line. The Displays page
arranges the displays by dragging, the Workspaces page picks the workspace
each app opens on, and the Idle page edits the swayidle command started from
the config.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from ..arrangement import Arrangement, Display
from ..rows import LineSpec, LinesGroup, OptionRow
from ..swayconfig import SwayConfig
from .base import ConfigApp, Option, Preview

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?")
ON_OFF = ["enabled", "disabled"]


def choice(key, doc, choices, default=""):
    return Option(key=key, doc=doc, defaults=[default], kind="choice", choices=choices)


def number(key, doc, default, minimum=0, maximum=10000, step=1, digits=0):
    return Option(key=key, doc=doc, defaults=[default], kind="number",
                  minimum=minimum, maximum=maximum, step=step, digits=digits)


def text(key, doc, default=""):
    return Option(key=key, doc=doc, defaults=[default])


CLIENT_COLORS = [
    ("focused", "#4c7899 #285577 #ffffff #2e9ef4 #285577", "The window that has focus."),
    ("focused_inactive", "#333333 #5f676a #ffffff #484e50 #5f676a",
     "The most recently focused view within a container which is not focused."),
    ("unfocused", "#333333 #222222 #888888 #292d2e #222222", "A view that does not have focus."),
    ("urgent", "#2f343a #900000 #ffffff #900000 #900000", "A view with an urgency hint."),
]

APPEARANCE = [
    text("font", "The font used for titlebars, e.g. `pango:JetBrains Mono 10`.", "monospace 10"),
    number("gaps inner", "Gap in pixels between adjacent windows.", "0", maximum=200),
    number("gaps outer", "Extra gap in pixels at the edges of each workspace. Can be negative.",
           "0", minimum=-200, maximum=200),
    choice("smart_gaps", "Only show gaps when a workspace has more than one window. "
           "`inverse_outer` only shows outer gaps when there's one window.",
           ["on", "off", "inverse_outer"], "off"),
    text("default_border", "Border style for new tiled windows: `none`, `normal [px]` "
         "(with titlebar) or `pixel [px]` (border only).", "normal 2"),
    text("default_floating_border", "Border style for new floating windows: `none`, "
         "`normal [px]` or `pixel [px]`.", "normal 2"),
    choice("hide_edge_borders", "Hide window borders adjacent to the screen edges.",
           ["none", "vertical", "horizontal", "both", "smart", "smart_no_gaps"], "none"),
    choice("smart_borders", "Hide borders when a workspace has only one window. `no_gaps` "
           "hides them only when gaps are also zero.", ["on", "off", "no_gaps"], "off"),
    text("titlebar_padding", "Titlebar padding in pixels: `<horizontal> [vertical]`.", "5 1"),
    number("titlebar_border_thickness", "Thickness of the titlebar border in pixels.", "1",
           maximum=50),
    choice("title_align", "Alignment of titlebar text.", ["left", "center", "right"], "left"),
    *[text(f"client.{name}", f"{doc}\n\nFive colors: border, background, text, indicator, "
           "child_border. The indicator shows where the next window will open.", default)
      for name, default, doc in CLIENT_COLORS],
]

BEHAVIOR = [
    choice("focus_follows_mouse", "Focus windows when the mouse moves over them. `always` also "
           "focuses when the mouse enters a window after a workspace switch.",
           ["yes", "no", "always"], "yes"),
    choice("mouse_warping", "Move the pointer when focus changes: to the new `output`, to the "
           "center of the `container`, or `none`.", ["output", "container", "none"], "output"),
    choice("focus_wrapping", "Whether moving focus past the edge of a container wraps around. "
           "`workspace` wraps only at the workspace edge.", ["yes", "no", "force", "workspace"],
           "yes"),
    choice("focus_on_window_activation", "What happens when a window requests focus.",
           ["smart", "urgent", "focus", "none"], "urgent"),
    choice("workspace_auto_back_and_forth", "Switching to the current workspace goes back to the "
           "previous one.", ["yes", "no"], "no"),
    choice("workspace_layout", "Layout for new workspaces.", ["default", "stacking", "tabbed"],
           "default"),
    choice("popup_during_fullscreen", "What to do with a popup that opens while a window is "
           "fullscreen.", ["smart", "ignore", "leave_fullscreen"], "smart"),
    text("floating_modifier", "Modifier held to drag floating windows with the mouse, and "
         "`normal` or `inverse` for which button resizes. E.g. `$mod normal`."),
    choice("tiling_drag", "Drag tiled windows with the floating modifier and left mouse button.",
           ["enable", "disable", "toggle"], "enable"),
    number("tiling_drag_threshold", "Pixels the pointer must move before a tiling drag starts.",
           "9", maximum=1000),
    choice("xwayland", "Support for X11 applications. `force` starts Xwayland immediately "
           "instead of on demand.", ["enable", "disable", "force"], "enable"),
    number("seat * hide_cursor", "Hide the mouse cursor after this many milliseconds without "
           "movement. 0 never hides it.", "0", maximum=600000, step=100),
    text("seat * xcursor_theme", "Cursor theme and size, e.g. `Adwaita 24`."),
]

KEYBOARD = [
    ("xkb_layout", "Keyboard layout(s), comma-separated, e.g. `us,de`.", "text", "us"),
    ("xkb_variant", "Layout variant(s), e.g. `dvorak`.", "text", ""),
    ("xkb_options", "XKB options, e.g. `caps:escape,compose:ralt`.", "text", ""),
    ("repeat_delay", "Milliseconds a key is held before it starts repeating.", (100, 2000, 10), "600"),
    ("repeat_rate", "Key repeats per second while held.", (1, 200, 1), "25"),
    ("xkb_numlock", "Turn Num Lock on at startup.", ON_OFF, "disabled"),
]
POINTER = [
    ("accel_profile", "`adaptive` speeds up with faster movement; `flat` is constant.",
     ["adaptive", "flat"], "adaptive"),
    ("pointer_accel", "Pointer speed, from -1 (slowest) to 1 (fastest).", (-1, 1, 0.05), "0"),
    ("natural_scroll", "Scrolling moves the content, like a touchscreen.", ON_OFF, "disabled"),
    ("left_handed", "Swap the left and right buttons.", ON_OFF, "disabled"),
    ("scroll_factor", "Multiplier for scroll speed.", (0.1, 10, 0.1), "1"),
    ("middle_emulation", "Clicking left and right together acts as a middle click.", ON_OFF,
     "disabled"),
]
TOUCHPAD = POINTER + [
    ("tap", "Tap to click.", ON_OFF, "disabled"),
    ("dwt", "Disable the touchpad while typing.", ON_OFF, "enabled"),
    ("click_method", "`clickfinger` decides the button by finger count; `button_areas` by "
     "where you press.", ["none", "button_areas", "clickfinger"], "button_areas"),
    ("tap_button_map", "Which button two- and three-finger taps press: left-right-middle or "
     "left-middle-right.", ["lrm", "lmr"], "lrm"),
]
INPUT_TYPES = {"keyboard": KEYBOARD, "pointer": POINTER, "touchpad": TOUCHPAD}

TRANSFORMS = ["normal", "90", "180", "270", "flipped", "flipped-90", "flipped-180", "flipped-270"]


def _input_option(input_type, prop, doc, spec, default):
    key = f"input type:{input_type} {prop}"
    if isinstance(spec, list):
        return choice(key, doc, spec, default)
    if isinstance(spec, tuple):
        lo, hi, step = spec
        digits = 0 if float(step).is_integer() else 2
        return number(key, doc, default, lo, hi, step, digits)
    return text(key, doc, default)


def _swaymsg(kind):
    try:
        out = subprocess.run(["swaymsg", "-r", "-t", kind], capture_output=True, text=True,
                             timeout=5, check=True).stdout
        return json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


# -- whole-line specs -----------------------------------------------------

def _regex_spec(noun, fields, pattern, template):
    regex = re.compile(pattern, re.S)

    def parse(body):
        m = regex.match(body)
        return [g.strip() for g in m.groups()] if m else None

    return LineSpec(noun, fields, parse, lambda values: template.format(*values))


BINDING = _regex_spec("binding", ["Keys", "Command"],
                      r"^bindsym((?:\s+--\S+)*\s+\S+)\s+(.+)$", "bindsym {} {}")
WINDOW_RULE = _regex_spec("window rule", ["Criteria", "Command"],
                          r"^for_window\s+(\[.*?\])\s+(.+)$", "for_window {} {}")
ASSIGN = _regex_spec("assignment", ["Criteria", "Target"],
                     r"^assign\s+(\[.*?\])\s+(.+)$", "assign {} {}")
VARIABLE = _regex_spec("variable", ["Name", "Value"], r"^set\s+(\$\S+)\s+(.+)$", "set {} {}")
EXEC = _regex_spec("command", ["Command"], r"^exec\s+(?!--)(.+)$", "exec {}")
EXEC_ALWAYS = _regex_spec("command", ["Command"], r"^exec_always\s+(.+)$", "exec_always {}")


# -- displays -------------------------------------------------------------

MODE_RE = re.compile(r"(\d+)x(\d+)(?:@([\d.]+)Hz)?")
POSITION_RE = re.compile(r"(-?\d+)(?:px)?\s+(-?\d+)(?:px)?")
GEOMETRY = ("mode", "scale", "transform", "position")
# (property, row title) for each display's group, in order
DISPLAY_ROWS = [("mode", "Resolution"), ("scale", "Scale"), ("transform", "Rotation"),
                ("position", "Position"), ("adaptive_sync", "Adaptive sync"),
                ("bg", "Wallpaper")]


def _output_label(out):
    return " ".join(p for p in (out.get("make"), out.get("model")) if p and p != "Unknown")


class DisplaysPage:
    """A drag-to-arrange layout of the connected displays, then their settings."""

    def __init__(self, state, on_change):
        self.state, self.on_change = state, on_change
        self.outputs = [o for o in _swaymsg("get_outputs") if o.get("active")]
        self.rows = {}
        self.groups = []

        arrange = Adw.PreferencesGroup(
            title="Arrangement",
            description="Drag displays to match how they sit on your desk; edges snap "
                        "together. Applies when you save.")
        if len(self.outputs) > 1:
            self.arrangement = Arrangement(self._displays(), self._on_moved)
            frame = Gtk.Frame(child=self.arrangement)
            frame.add_css_class("view")
            arrange.add(frame)
        else:
            self.arrangement = None
            arrange.set_description("Connect another display to arrange them.")
        self.groups.append(arrange)

        everywhere = Adw.PreferencesGroup(title="All displays")
        self._add_row(everywhere, "output * bg", "Wallpaper")
        self._add_row(everywhere, "output * adaptive_sync", "Adaptive sync")
        self.groups.append(everywhere)

        for out in self.outputs:
            name = out["name"]
            group = Adw.PreferencesGroup(title=name, description=_output_label(out))
            for prop, title in DISPLAY_ROWS:
                self._add_row(group, f"output {name} {prop}", title)
            self.groups.append(group)

    def _add_row(self, group, key, title):
        opt = self.state.by_key.get(key)
        if opt is None:
            return
        row = OptionRow(opt, self.state, self._on_row_changed, title)
        if not key.startswith("output * "):
            # The group already names the display; the full doc is behind the info button.
            if isinstance(row.widget, Adw.EntryRow):
                row.widget.set_tooltip_text(None)
            else:
                row.widget.set_subtitle("")
        group.add(row.widget)
        self.rows[key] = row

    def _displays(self):
        """The displays as the saved-or-pending config will lay them out."""
        displays = []
        for out in self.outputs:
            name = out["name"]
            user = lambda prop: (self.state.user(f"output {name} {prop}") or [""])[0]
            current = out.get("current_mode") or {}
            width, height = current.get("width", 1920), current.get("height", 1080)
            refresh = current.get("refresh", 60000) / 1000
            if m := MODE_RE.match(user("mode")):
                width, height = int(m.group(1)), int(m.group(2))
                refresh = float(m.group(3)) if m.group(3) else refresh
            try:
                scale = float(user("scale") or out.get("scale") or 1)
            except ValueError:
                scale = out.get("scale") or 1
            if (user("transform") or out.get("transform", "normal")).endswith(("90", "270")):
                width, height = height, width
            x, y = out["rect"]["x"], out["rect"]["y"]
            if m := POSITION_RE.fullmatch(user("position")):
                x, y = int(m.group(1)), int(m.group(2))
            detail = f"{width}×{height} @ {refresh:.0f} Hz" + (f", {scale:g}×" if scale != 1 else "")
            displays.append(Display(name, _output_label(out), detail, x, y,
                                    round(width / scale), round(height / scale)))
        return displays

    def _on_moved(self, displays):
        for d in displays:
            key = f"output {d.name} position"
            value = f"{d.x} {d.y}"
            if (self.state.user(key) or [None])[0] != value:
                self.state.set(key, [value])
                if key in self.rows:
                    self.rows[key].refresh()
        self.on_change()

    def _on_row_changed(self, key):
        if self.arrangement and key.split()[-1] in GEOMETRY:
            self.arrangement.set_displays(self._displays())
        self.on_change()


# -- idle (swayidle) ------------------------------------------------------

SCREENSAVER = "~/.local/bin/screensaver-launch"
SCREENSAVER_FONT_SIZE = 20  # the launcher's default
FONT_SIZE_RE = re.compile(r"\s*--font-size[ =](\d+)")
LOCK = "swaylock -f -c 1e1e2e"
# (label, doc, matches(command), command, resume command, default minutes)
IDLE_TIMEOUTS = [
    ("Screensaver", "Minutes idle before the screensaver starts. Any input closes it.",
     lambda cmd: "screensaver" in cmd, SCREENSAVER, f"{SCREENSAVER} stop", 5),
    ("Lock screen", "Minutes idle before the screen locks.",
     lambda cmd: "swaylock" in cmd, LOCK, None, 30),
    ("Turn off displays", "Minutes idle before the displays power off.",
     lambda cmd: "power off" in cmd or "dpms off" in cmd,
     'swaymsg "output * power off"', 'swaymsg "output * power on"', 120),
]
# swayidle events that take a command but no timeout
IDLE_EVENTS = {"before-sleep", "after-resume", "lock", "unlock"}


def _parse_swayidle(body):
    """Split `exec swayidle ...` into (flags, events).

    Events are dicts: {"event": "timeout", "seconds", "command", "resume"}, or
    {"event": <name>, "command"}; idlehint is kept as {"event", "seconds"}.
    """
    try:
        tokens = shlex.split(body)
    except ValueError:
        return None
    if tokens[:2] != ["exec", "swayidle"]:
        return None
    flags, events, i = [], [], 2
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            flags.append(tok)
            i += 1
        elif tok == "timeout" and i + 2 < len(tokens):
            event = {"event": tok, "seconds": tokens[i + 1], "command": tokens[i + 2],
                     "resume": None}
            i += 3
            if i + 1 < len(tokens) and tokens[i] == "resume":
                event["resume"] = tokens[i + 1]
                i += 2
            events.append(event)
        elif tok in IDLE_EVENTS and i + 1 < len(tokens):
            events.append({"event": tok, "command": tokens[i + 1]})
            i += 2
        elif tok == "idlehint" and i + 1 < len(tokens):
            events.append({"event": tok, "seconds": tokens[i + 1]})
            i += 2
        else:
            return None  # something we don't understand; leave the line alone
    return flags, events


def _format_swayidle(flags, events):
    parts = [" ".join(["exec swayidle", *flags])]
    for e in events:
        if e["event"] == "timeout":
            parts.append(f"    timeout {e['seconds']} {shlex.quote(e['command'])}")
            if e["resume"]:
                parts.append(f"         resume  {shlex.quote(e['resume'])}")
        elif e["event"] == "idlehint":
            parts.append(f"    idlehint {e['seconds']}")
        else:
            parts.append(f"    {e['event']} {shlex.quote(e['command'])}")
    return " \\\n".join(parts)


class IdleGroup:
    """Timeouts for the swayidle command started with `exec swayidle`."""

    def __init__(self, cfg, on_change):
        self.cfg, self.on_change = cfg, on_change
        self.widget = Adw.PreferencesGroup(
            title="Idle", description="Edits the swayidle command under Startup. "
                                      "Takes effect the next time you log in.")
        line, parsed = self._find()
        if line is not None and parsed is None:
            self.widget.set_description("The swayidle command under Startup couldn't be "
                                        "parsed; edit it there.")
            return
        events = parsed[1] if parsed else []
        screensaver = next((e for e in events if e["event"] == "timeout"
                            and "screensaver" in e["command"]), None)
        m = FONT_SIZE_RE.search(screensaver["command"]) if screensaver else None
        self.font_size = int(m.group(1)) if m else SCREENSAVER_FONT_SIZE
        for label, doc, matches, command, resume, default in IDLE_TIMEOUTS:
            current = next((e for e in events if e["event"] == "timeout"
                            and matches(e["command"])), None)
            minutes = int(current["seconds"]) / 60 if current else 0
            subtitle = doc + " 0 turns it off."
            if command == SCREENSAVER and not Path(SCREENSAVER).expanduser().exists():
                subtitle += f" Needs {SCREENSAVER}."
            adjustment = Gtk.Adjustment(value=minutes, lower=0, upper=1440, step_increment=1,
                                        page_increment=10)
            row = Adw.SpinRow(title=label, subtitle=subtitle, adjustment=adjustment,
                              digits=0 if minutes.is_integer() else 1)
            row.connect("notify::value", self._on_timeout, matches, command, resume)
            self.widget.add(row)
            if command == SCREENSAVER:
                font = Adw.SpinRow(title="Screensaver font size",
                                   subtitle="Font size in points for the screensaver animation.",
                                   adjustment=Gtk.Adjustment(value=self.font_size, lower=6,
                                                             upper=72, step_increment=1,
                                                             page_increment=4))
                font.connect("notify::value", self._on_font_size)
                self.widget.add(font)
        sleep = Adw.SwitchRow(title="Lock before sleep",
                              subtitle="Lock the screen when the computer suspends.",
                              active=any(e["event"] == "before-sleep" and "swaylock" in e["command"]
                                         for e in events))
        sleep.connect("notify::active", self._on_sleep)
        self.widget.add(sleep)

    def _find(self):
        for line in self.cfg.commands({"exec"}):
            if line.body.split()[1:2] == ["swayidle"]:
                return line, _parse_swayidle(line.body)
        return None, None

    def _edit(self, change):
        line, parsed = self._find()
        if line is None:
            flags, events = ["-w"], []
        elif parsed is None:
            return
        else:
            flags, events = parsed
        change(events)
        if not events:
            if line is not None:
                self.cfg.remove_line(line)
        elif line is None:
            execs = self.cfg.commands({"exec"})
            self.cfg.add_line(_format_swayidle(flags, events),
                              after=execs[-1] if execs else None)
        else:
            self.cfg.replace_line(line, _format_swayidle(flags, events))
        self.on_change()

    def _on_timeout(self, row, _, matches, command, resume):
        seconds = round(row.get_value() * 60)

        def change(events):
            index = next((i for i, e in enumerate(events) if e["event"] == "timeout"
                          and matches(e["command"])), None)
            if seconds == 0:
                if index is not None:
                    del events[index]
            elif index is not None:
                events[index]["seconds"] = str(seconds)
            else:
                # Keep timeouts in order, ahead of the other events.
                at = next((i for i, e in enumerate(events) if e["event"] != "timeout"
                           or int(e["seconds"]) > seconds), len(events))
                new = self._screensaver_command(command) if command == SCREENSAVER else command
                events.insert(at, {"event": "timeout", "seconds": str(seconds),
                                   "command": new, "resume": resume})
        self._edit(change)

    def _screensaver_command(self, command):
        command = FONT_SIZE_RE.sub("", command)
        if self.font_size != SCREENSAVER_FONT_SIZE:
            command += f" --font-size {self.font_size}"
        return command

    def _on_font_size(self, row, _):
        self.font_size = round(row.get_value())

        def change(events):
            for e in events:
                if e["event"] == "timeout" and "screensaver" in e["command"]:
                    e["command"] = self._screensaver_command(e["command"])
        self._edit(change)

    def _on_sleep(self, row, _):
        def change(events):
            events[:] = [e for e in events
                         if not (e["event"] == "before-sleep" and "swaylock" in e["command"])]
            if row.get_active():
                events.append({"event": "before-sleep", "command": LOCK})
        self._edit(change)


# -- workspaces -----------------------------------------------------------

WS_NAME = r'("[^"]*"|[^\s,;]+)'
# for_window [criteria] move container to workspace D, focus
ROUTE_RE = re.compile(r"^for_window\s+(\[.*?\])\s+move\s+(?:container\s+|window\s+)?(?:to\s+)?"
                      rf"workspace\s+(?:number\s+)?{WS_NAME}(\s*,\s*focus)?\s*$")
# assign [criteria] [→] [workspace] [number] D
ASSIGN_WS_RE = re.compile(r"^assign\s+(\[.*?\])\s+(?:→\s+)?(?:workspace\s+)?(?:number\s+)?"
                          rf"{WS_NAME}\s*$")
WS_COMMAND_RE = re.compile(rf"\bworkspace\s+(?:number\s+)?{WS_NAME}\s*$")
WS_KEYWORDS = {"next", "prev", "next_on_output", "prev_on_output", "back_and_forth", "current"}
CRITERION_RE = re.compile(r'^\[\s*(app_id|class)\s*=\s*"([^"]*)"\s*\]$')
# The "everything else" rule. tiling skips dialogs and other windows that open
# floating, so they stay with their parent. It must come before the app rules:
# sway runs every matching for_window rule in config order, so a later app rule
# moves its windows on again.
CATCH_ALL = "[tiling]"
MATCH_KINDS = [("app_id", "Wayland app ID"), ("class", "XWayland class")]


def _route(line):
    """(match, criteria, workspace) for a line that sends windows to a workspace."""
    body = line.body
    m = ROUTE_RE.match(body) if body.startswith("for_window") else ASSIGN_WS_RE.match(body)
    if m and m.group(2) not in WS_KEYWORDS:
        return m, m.group(1), m.group(2).strip('"')
    return None


def _app_name(value):
    """A criteria value without its case flag and anchors: (?i)^signal -> signal."""
    return re.sub(r"^\(\?i\)", "", value).strip("^$")


def _app_key(line):
    """What makes two rules the same app: app_id and class rules for one name
    (ignoring case) that send it to the same workspace."""
    _, criteria, workspace = _route(line)
    if m := CRITERION_RE.match(criteria):
        return _app_name(m.group(2)).lower(), workspace, line.body.split()[0]
    return None


def _workspace_names(cfg, routes):
    """Workspaces named in the config or open now; named ones before numbered."""
    names = []
    for line in cfg.commands({"bindsym", "bindcode"}) + [l for b in cfg.blocks("mode")
                                                         for l in cfg.commands({"bindsym"}, b)]:
        if (m := WS_COMMAND_RE.search(line.body)) and m.group(1) not in WS_KEYWORDS:
            names.append(m.group(1).strip('"'))
    names += [ws for _, _, ws in routes] + [w.get("name") for w in _swaymsg("get_workspaces")]
    names = list(dict.fromkeys(n for n in names if n))
    return [n for n in names if not n.isdigit()] + sorted((n for n in names if n.isdigit()), key=int)


def _format_workspace(name):
    return f'"{name}"' if " " in name else name


def _running_apps():
    """[(match kind, value)] for the windows open now."""
    apps = []

    def walk(node):
        if node.get("pid"):
            if node.get("app_id"):
                apps.append(("app_id", node["app_id"]))
            elif cls := (node.get("window_properties") or {}).get("class"):
                apps.append(("class", cls))
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            walk(child)

    tree = _swaymsg("get_tree")
    if tree:
        walk(tree)
    return sorted(set(apps), key=lambda a: a[1].lower())


class WorkspacesPage:
    """Which workspace each app opens on, and where everything else goes."""

    def __init__(self, cfg, on_change):
        self.cfg, self.on_change = cfg, on_change
        routes = [(line, *_route(line)[1:]) for line in cfg.commands({"for_window", "assign"})
                  if _route(line)]
        self.catch_all = next((line for line, criteria, _ in routes if criteria == CATCH_ALL), None)
        self.routes = [line for line, criteria, _ in routes if criteria != CATCH_ALL]
        self.workspaces = _workspace_names(cfg, routes)

        self.apps = Adw.PreferencesGroup(
            title="Apps", description="Open an app's windows on a workspace. Focus follows "
                                      "the app there.")
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                         tooltip_text="Add app")
        add.add_css_class("flat")
        add.connect("clicked", self._add_dialog)
        self.apps.set_header_suffix(add)
        # Most apps have a rule for their Wayland app ID and another for their
        # XWayland class; show such a pair as one row.
        rows = {}
        for line in self.routes:
            rows.setdefault(_app_key(line) or line, []).append(line)
        for lines in rows.values():
            self._add_row(lines)

        self.other = Adw.PreferencesGroup(title="Everything else")
        model = Gtk.StringList.new(["Where it was opened", *self.workspaces])
        current = _route(self.catch_all)[2] if self.catch_all else None
        if current and current not in self.workspaces:
            model.append(current)
        self.other_row = Adw.ComboRow(
            title="Open other apps on", model=model,
            subtitle="Apps not listed above. Dialogs and other floating windows stay where "
                     "they open.")
        if current:
            self.other_row.set_selected(self._index(model, current))
        self.other_row.connect("notify::selected", self._on_other)
        self.other.add(self.other_row)
        if any(line.body.startswith("assign") for line in self.routes):
            self.other.set_description("Apps placed with assign are moved here too; "
                                       "re-add them above to keep them on their workspace.")
        self.groups = [self.apps, self.other, self._displays_group()]

    def _displays_group(self):
        group = Adw.PreferencesGroup(
            title="Displays", description="The display each workspace opens on. Applies "
                                           "when the workspace is next created.")
        outputs = [(o["name"], o.get("model") if o.get("model") != "Unknown" else "")
                   for o in _swaymsg("get_outputs") if o.get("active")]
        for name in self.workspaces:
            current = (self.cfg.get(f"workspace {name} output") or [""])[0]
            choices = [""] + [n for n, _ in outputs]
            labels = ["Any display"] + [f"{n} · {label}" if label else n for n, label in outputs]
            if current and current not in choices:
                choices.append(current)  # e.g. a fallback list, or a display that's unplugged
                labels.append(current)
            row = Adw.ComboRow(title=name, model=Gtk.StringList.new(labels), use_markup=False)
            row.set_selected(choices.index(current))
            row.connect("notify::selected", self._on_display, name, choices)
            group.add(row)
        return group

    def _on_display(self, row, _, name, choices):
        output = choices[row.get_selected()]
        self.cfg.set(f"workspace {name} output", [output] if output else None)
        self.on_change()

    @staticmethod
    def _index(model, name):
        return next(i for i in range(model.get_n_items()) if model.get_string(i) == name)

    def _add_row(self, lines):
        line = lines[0]
        _, criteria, workspace = _route(line)
        matches = [CRITERION_RE.match(_route(l)[1]) for l in lines]
        if all(matches):
            title = _app_name(matches[0].group(2))
            kinds = {m.group(1) for m in matches}
            subtitle = ("Wayland and XWayland" if len(kinds) > 1
                        else dict(MATCH_KINDS)[kinds.pop()])
        else:
            title, subtitle = criteria, ""
        if line.body.startswith("assign"):
            subtitle = ", ".join(filter(None, [subtitle, "assign (focus stays put)"]))
        model = Gtk.StringList.new(self.workspaces)
        if workspace not in self.workspaces:
            model.append(workspace)
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=model, use_markup=False)
        row.set_selected(self._index(model, workspace))
        row.connect("notify::selected", self._on_workspace, lines)
        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Remove")
        remove.add_css_class("flat")
        remove.connect("clicked", self._remove, lines, row)
        row.add_suffix(remove)
        self.apps.add(row)

    def _set_workspace(self, line, name):
        m = _route(line)[0]
        body = line.body
        self.cfg.replace_line(line, body[:m.start(2)] + _format_workspace(name) + body[m.end(2):])

    def _on_workspace(self, row, _, lines):
        for line in lines:
            self._set_workspace(line, row.get_model().get_string(row.get_selected()))
        self.on_change()

    def _remove(self, _button, lines, row):
        for line in lines:
            self.cfg.remove_line(line)
            self.routes.remove(line)
        self.apps.remove(row)
        self.on_change()

    def _on_other(self, row, _):
        index = row.get_selected()
        if index == Gtk.INVALID_LIST_POSITION:
            return
        if index == 0:
            if self.catch_all:
                self.cfg.remove_line(self.catch_all)
                self.catch_all = None
        elif self.catch_all:
            self._set_workspace(self.catch_all, row.get_model().get_string(index))
        else:
            body = (f"for_window {CATCH_ALL} move container to workspace "
                    f"{_format_workspace(row.get_model().get_string(index))}, focus")
            self.catch_all = self._insert_before_routes(body)
        self.on_change()

    def _insert_before_routes(self, body):
        lines = self.cfg.lines
        if not self.routes:
            rules = self.cfg.commands({"for_window"})
            return self.cfg.add_line(body, after=rules[-1] if rules else None)
        at = lines.index(self.routes[0])
        if at == 0:
            line = self.cfg.add_line(body, after=self.routes[0])
            lines.insert(0, lines.pop(lines.index(line)))
            return line
        return self.cfg.add_line(body, after=lines[at - 1])

    def _add_dialog(self, *_):
        routed = {CRITERION_RE.match(_route(l)[1]).groups()
                  for l in self.routes if CRITERION_RE.match(_route(l)[1])}
        running = [a for a in _running_apps() if a not in routed]

        dialog = Adw.AlertDialog(heading="Add app",
                                 body="Pick an open window, or type its app ID.")
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        box.add_css_class("boxed-list")
        pick = Adw.ComboRow(title="Open windows",
                            model=Gtk.StringList.new(["Choose…"] + [v for _, v in running]))
        entry = Adw.EntryRow(title="App ID or class", activates_default=True)
        kind = Adw.ComboRow(title="Match", model=Gtk.StringList.new([l for _, l in MATCH_KINDS]))
        workspace = Adw.ComboRow(title="Workspace", model=Gtk.StringList.new(self.workspaces))

        def on_pick(row, _):
            index = row.get_selected()
            if 0 < index <= len(running):
                match, value = running[index - 1]
                entry.set_text(value)
                kind.set_selected([k for k, _ in MATCH_KINDS].index(match))
        pick.connect("notify::selected", on_pick)
        if running:
            box.append(pick)
        for row in (entry, kind, workspace):
            box.append(row)
        box.set_size_request(460, -1)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_add, entry, kind, workspace)
        dialog.present(self.apps)

    def _on_add(self, _dialog, response, entry, kind, workspace):
        value = entry.get_text().strip().replace('"', "")
        if response != "add" or not value or not self.workspaces:
            return
        match = MATCH_KINDS[kind.get_selected()][0]
        name = self.workspaces[workspace.get_selected()]
        body = (f'for_window [{match}="{value}"] move container to workspace '
                f"{_format_workspace(name)}, focus")
        if self.routes:
            after = self.routes[-1]
        else:
            after = self.catch_all or (self.cfg.commands({"for_window"}) or [None])[-1]
        line = self.cfg.add_line(body, after=after)
        self.routes.append(line)
        self._add_row([line])
        self.on_change()


class Sway(ConfigApp):
    id = "sway"
    name = "Sway"
    icon = "video-display-symbolic"
    categories = [
        ("Appearance", "preferences-desktop-appearance-symbolic"),
        ("Behavior", "preferences-system-symbolic"),
        ("Displays", "video-display-symbolic"),
        ("Input", "input-mouse-symbolic"),
        ("Keybindings", "input-keyboard-symbolic"),
        ("Workspaces", "view-app-grid-symbolic"),
        ("Window Rules", "view-grid-symbolic"),
        ("Startup", "system-run-symbolic"),
        ("Idle", "preferences-desktop-screensaver-symbolic"),
        ("Variables", "accessories-text-editor-symbolic"),
    ]

    def available(self):
        return shutil.which("sway") is not None

    def config_path(self):
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "sway" / "config"

    def open_config(self, path):
        return SwayConfig(path, fallback="/etc/sway/config")

    def load_options(self, cfg):
        options = []
        for category, group in [("Appearance", APPEARANCE), ("Behavior", BEHAVIOR)]:
            for opt in group:
                opt.category = category
                options.append(opt)
        options += self._display_options()
        options += self._input_options()
        return options

    def _display_options(self):
        options = [
            text("output * bg", "Wallpaper for every display: `<file> <mode>` (fill, fit, "
                 "stretch, center, tile) or `<#color> solid_color`."),
            choice("output * adaptive_sync", "Variable refresh rate (FreeSync / G-Sync) for "
                   "every display.", ["on", "off"], "off"),
        ]
        for out in _swaymsg("get_outputs"):
            name = out["name"]
            label = _output_label(out)
            modes = list(dict.fromkeys(
                f"{m['width']}x{m['height']}@{m['refresh'] / 1000:.3f}Hz" for m in out.get("modes", [])))
            about = f"{name}" + (f" ({label})" if label else "")
            options += [
                choice(f"output {name} mode", f"Resolution and refresh rate for {about}.", modes),
                number(f"output {name} scale", f"Scale factor for {about}. Currently "
                       f"{out.get('scale', 1)}.", "1", 0.5, 4, 0.05, 2),
                text(f"output {name} position", f"Position of {about} in the layout, as "
                     f"`<x> <y>` in pixels."),
                choice(f"output {name} transform", f"Rotation of {about}.", TRANSFORMS, "normal"),
                choice(f"output {name} adaptive_sync", f"Variable refresh rate for {about}.",
                       ["on", "off"], "off"),
                text(f"output {name} bg", f"Wallpaper for {about} only."),
            ]
        for opt in options:
            opt.category = "Displays"
        return options

    def _input_options(self):
        present = {i.get("type") for i in _swaymsg("get_inputs")} or {"keyboard", "pointer"}
        options = []
        for input_type, props in INPUT_TYPES.items():
            if input_type in present:
                for prop, doc, spec, default in props:
                    opt = _input_option(input_type, prop, doc, spec, default)
                    opt.category = "Input"
                    options.append(opt)
        return options

    def build_page(self, category, state, on_change):
        cfg = state.cfg
        if category == "Keybindings":
            groups = [LinesGroup("Default mode", cfg, cfg.commands({"bindsym"}), BINDING, on_change,
                                 description="Variables like $mod are defined under Variables.")]
            for block in cfg.blocks("mode"):
                name = block[0].split(None, 1)[1].strip('"')
                groups.append(LinesGroup(f"Mode: {name}", cfg, cfg.commands({"bindsym"}, block),
                                         BINDING, on_change, block=block))
        elif category == "Workspaces":
            return WorkspacesPage(cfg, on_change).groups
        elif category == "Window Rules":
            unrouted = lambda lines: [l for l in lines if not _route(l)]
            groups = [
                LinesGroup("Window rules", cfg, unrouted(cfg.commands({"for_window"})),
                           WINDOW_RULE, on_change,
                           description="Run a command when a matching window opens. Rules "
                                       "that move apps to a workspace are under Workspaces."),
                LinesGroup("Assignments", cfg, unrouted(cfg.commands({"assign"})), ASSIGN,
                           on_change, description="Open matching windows on an output."),
            ]
        elif category == "Startup":
            groups = [
                LinesGroup("Run at login", cfg, cfg.commands({"exec"}), EXEC, on_change,
                           description="exec: runs once when Sway starts."),
                LinesGroup("Run on every reload", cfg, cfg.commands({"exec_always"}), EXEC_ALWAYS,
                           on_change, description="exec_always: runs at startup and after "
                                                  "each config reload."),
            ]
        elif category == "Displays":
            return DisplaysPage(state, on_change).groups
        elif category == "Idle":
            groups = [IdleGroup(cfg, on_change)]
        elif category == "Variables":
            groups = [LinesGroup("Variables", cfg, cfg.commands({"set"}), VARIABLE, on_change,
                                 description="Names start with $, e.g. $mod or $term.")]
        else:
            return None
        return [g.widget for g in groups]

    def previews(self):
        keys = frozenset(f"client.{name}" for name, _, _ in CLIENT_COLORS) | {"font"}
        return [Preview("Appearance", keys, self._border_preview)]

    def _border_preview(self, state):
        font = state.first("font").removeprefix("pango:") or "monospace 10"
        rows = []
        for name, default, _ in CLIENT_COLORS:
            colors = [c[:7] for c in state.first(f"client.{name}").split() if HEX_RE.fullmatch(c)]
            if len(colors) < 4:
                colors = default.split()
            border, bg, fg, indicator = colors[:4]
            title = GLib.markup_escape_text(f" {name.replace('_', ' ')} window ".ljust(28))
            rows.append(f'<span background="{border}"> </span>'
                        f'<span background="{bg}" foreground="{fg}">{title}</span>'
                        f'<span background="{indicator}"> </span>'
                        f'<span background="{border}"> </span>')
        return f'<span font_desc="{GLib.markup_escape_text(font)}">' + "\n\n".join(rows) + "</span>"

    def validate(self, path, target):
        result = subprocess.run(["sway", "-C", "-c", str(path)], capture_output=True, text=True,
                                timeout=30)
        output = result.stdout + result.stderr
        errors = [re.sub(rf"\s*\({re.escape(str(path))}\)$", "", m.group(1))
                  for m in re.finditer(r"Error on (line \d+ .*)$", output, re.M)]
        if errors:
            return "\n".join(errors)
        if result.returncode != 0:
            # Skip sway's unrelated warnings, e.g. the proprietary Nvidia notice.
            lines = [l for l in output.splitlines() if "[ERROR]" in l and "Nvidia" not in l]
            return "\n".join(lines) or "Sway rejected the config."
        return None

    def apply(self):
        if subprocess.run(["swaymsg", "reload"], capture_output=True).returncode == 0:
            return "Saved and reloaded Sway"
        return "Saved Sway config"
