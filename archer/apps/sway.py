"""Sway window manager.

Sway has no machine-readable option list, so the settings here are curated
from sway(5), sway-input(5) and sway-output(5). Displays and input devices
come from the running session via swaymsg. Keybindings, window rules,
startup commands and variables are edited line by line.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from gi.repository import GLib

from ..rows import LineSpec, LinesGroup
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
        ("Window Rules", "view-grid-symbolic"),
        ("Startup", "system-run-symbolic"),
        ("Variables", "accessories-text-editor-symbolic"),
    ]

    def available(self):
        return shutil.which("sway") is not None

    def config_path(self):
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "sway" / "config"

    def open_config(self, path):
        return SwayConfig(path, fallback="/etc/sway/config")

    def load_options(self):
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
            label = " ".join(p for p in (out.get("make"), out.get("model")) if p and p != "Unknown")
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
        elif category == "Window Rules":
            groups = [
                LinesGroup("Window rules", cfg, cfg.commands({"for_window"}), WINDOW_RULE,
                           on_change, description="Run a command when a matching window opens."),
                LinesGroup("Assignments", cfg, cfg.commands({"assign"}), ASSIGN, on_change,
                           description="Open matching windows on a workspace or output."),
            ]
        elif category == "Startup":
            groups = [
                LinesGroup("Run at login", cfg, cfg.commands({"exec"}), EXEC, on_change,
                           description="exec: runs once when Sway starts."),
                LinesGroup("Run on every reload", cfg, cfg.commands({"exec_always"}), EXEC_ALWAYS,
                           on_change, description="exec_always: runs at startup and after "
                                                  "each config reload."),
            ]
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

    def validate(self, path):
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
