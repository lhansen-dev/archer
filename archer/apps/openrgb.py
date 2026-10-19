"""Lighting controls backed by the installed OpenRGB CLI."""

import os
import shutil
import subprocess
from pathlib import Path

from gi.repository import Adw

from .. import rgb
from ..configfile import KeyValueConfig, parse_text
from .base import ConfigApp, Option


class OpenRGB(ConfigApp):
    id = "openrgb"
    name = "RGB Lighting"
    icon = "preferences-color-symbolic"

    def available(self):
        return bool(shutil.which(self.executable(KeyValueConfig(self.config_path()))))

    @staticmethod
    def executable(cfg):
        return os.path.expanduser((cfg.get("openrgb.executable") or ["openrgb"])[0] or "openrgb")

    def config_path(self):
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "archer/rgb.conf"

    def load_options(self, cfg):
        self.devices = []
        self.error = ""
        try:
            self.devices = rgb.discover(executable=self.executable(cfg))
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            self.error = str(error)
        self.categories = [("Devices", "computer-symbolic"),
                           ("All devices", "preferences-color-symbolic"),
                           ("OpenRGB", "preferences-system-symbolic")]
        options = [Option(
            key="all.color", label="Color", kind="color", defaults=[""], category="All devices",
            doc="Set a shared color for every detected device when you save. Device and zone "
                "colors override this. Without an explicit effect, uses Static or Direct mode. "
                "Animated effects may ignore custom colors."),
            Option(key="openrgb.executable", label="OpenRGB executable", kind="text",
                   defaults=["openrgb"], category="OpenRGB",
                   doc="Use the system OpenRGB or enter the full path to an OpenRGB AppImage. "
                       "OpenRGB 1.1 fixes concurrent hardware writes that can cause incorrect "
                       "GPU colors in 1.0. Restart Archer after changing this setting.")]
        for device in self.devices:
            category = f"{device.name} · {device.index + 1}"
            self.categories.append((category, "preferences-color-symbolic"))
            options.extend([
                Option(key=device.key + ".color", label="Color", kind="color", defaults=[""],
                       category=category, doc="Override the shared color for this device."),
                Option(key=device.key + ".mode", label="Effect", kind="choice", defaults=[""],
                       choices=device.modes, category=category,
                       doc="Choose a device effect. Default uses Static or Direct when setting "
                           "a color; otherwise it leaves the current effect unchanged."),
            ])
            for index, zone in enumerate(device.zones):
                options.append(Option(
                    key=f"{device.key}.zone-{index}.color", label=f"{zone} color", kind="color",
                    defaults=[""], category=category,
                    doc="Override this lighting zone's color. Empty addressable headers may "
                        "need their LED count configured in OpenRGB first."))
        # Preserve settings for disconnected hardware and make them visible in Modified.
        known = {option.key for option in options}
        for key in cfg.keys():
            if key not in known:
                options.append(Option(key=key, kind="text", defaults=[""], hidden=True,
                                      doc="Saved setting for a device that is currently unavailable."))
        return options

    def build_page(self, category, state, on_change):
        if category != "Devices":
            return None
        group = Adw.PreferencesGroup(
            title="Detected lighting controllers",
            description="Colors and effects apply when you save. Restart Archer to refresh this "
                        "list. Fans and strips appear under their controller's zones.")
        if self.error or not self.devices:
            group.add(Adw.ActionRow(
                title="OpenRGB detection failed" if self.error else "No devices detected",
                subtitle=self.error or "Check device access in OpenRGB, then restart Archer.",
                use_markup=False, subtitle_lines=0))
        for device in self.devices:
            group.add(Adw.ActionRow(title=f"{device.index + 1}. {device.name}",
                                   subtitle=f"{device.kind} · " + ", ".join(device.zones),
                                   use_markup=False, subtitle_lines=0))
        return [group]

    def validate(self, candidate, target):
        try:
            cfg = KeyValueConfig(candidate)
            if not shutil.which(self.executable(cfg)):
                raise ValueError("OpenRGB executable was not found or is not executable")
            for key, values in parse_text(candidate.read_text()).items():
                if len(values) != 1:
                    raise ValueError(f"{key}: expected one value")
                if values[0] and key.endswith(".color"):
                    rgb.color(values[0])
            rgb.commands(self.devices, cfg)
        except ValueError as error:
            return str(error)
        return None

    def apply(self):
        try:
            cfg = KeyValueConfig(self.config_path())
            executable = self.executable(cfg)
            devices = rgb.discover(executable=executable)
            current = {device.key: device for device in devices}
            for device in self.devices:
                if device.key not in current and any(
                        key.startswith(device.key + ".") for key in cfg.keys()):
                    raise ValueError(f"{device.name} is no longer detected; restart Archer")
                if device.key in current and device.zones != current[device.key].zones:
                    raise ValueError(f"{device.name} zones changed; restart Archer")
            args = rgb.commands(devices, cfg)
            if not args:
                return "Saved RGB settings; no lighting changes requested"
            rgb.run_openrgb(args, executable=executable)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            return f"Saved RGB settings; lighting could not be applied: {error}"
        return "Saved and applied RGB lighting"
