"""OpenRGB CLI discovery and command construction (no hardware writes on discovery)."""

from dataclasses import dataclass, field
import hashlib
import re
import shlex
import subprocess


@dataclass
class Device:
    index: int
    name: str
    kind: str = ""
    location: str = ""
    serial: str = ""
    modes: list[str] = field(default_factory=list)
    zones: list[str] = field(default_factory=list)

    @property
    def key(self):
        # CLI indices can change between scans; persist the hardware identity instead.
        identity = "\0".join((self.name, self.kind, self.serial or self.location))
        return "device-" + hashlib.sha256(identity.encode()).hexdigest()[:20]


def parse_devices(text):
    devices = []
    device = None
    location_continues = False
    for line in text.splitlines():
        if match := re.fullmatch(r"(\d+): (.+)", line):
            device = Device(int(match[1]), match[2])
            devices.append(device)
            location_continues = False
        elif device and (match := re.match(r"\s+(Type|Location|Serial|Modes|Zones):\s*(.*)", line)):
            label, value = match.groups()
            location_continues = label == "Location"
            if label in ("Modes", "Zones"):
                # OpenRGB quotes multiword names and brackets the active mode.
                values = shlex.split(re.sub(
                    r"\[([^]]+)\]", lambda m: shlex.quote(m[1].strip("'\"")), value))
                setattr(device, label.lower(), values)
            else:
                setattr(device, {"Type": "kind", "Location": "location", "Serial": "serial"}[label], value)
        elif location_continues and line.startswith(" ") and ":" not in line:
            device.location += " " + line.strip()
        else:
            location_continues = False
    keys = [d.key for d in devices]
    if len(keys) != len(set(keys)):
        raise ValueError("OpenRGB returned devices with indistinguishable identities")
    return devices


def run_openrgb(args, executable="openrgb"):
    result = subprocess.run([executable, *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError((result.stderr.strip() or result.stdout.strip() or
                          f"OpenRGB exited with status {result.returncode}")[-1500:])
    return result.stdout


def discover(executable="openrgb"):
    return parse_devices(run_openrgb(["--list-detailed"], executable=executable))


def color(value):
    value = value.removeprefix("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError("Colors must contain six hex digits, such as #ff8800")
    return value


def commands(devices, cfg):
    """Build a single CLI invocation, with explicit device and zone selectors."""
    def value(key):
        return (cfg.get(key) or [""])[0]

    global_color = value("all.color")
    args = []
    for device in devices:
        prefix = device.key
        selected_color = value(prefix + ".color") or global_color
        mode = value(prefix + ".mode")
        zones = [(i, value(f"{prefix}.zone-{i}.color")) for i in range(len(device.zones))]
        zones = [(i, c) for i, c in zones if c]
        if not (selected_color or mode or zones):
            continue
        if mode and mode not in device.modes:
            raise ValueError(f"{device.name} no longer supports mode {mode}")
        if not mode and (selected_color or zones):
            mode = next((m for m in device.modes if m.lower() == "static"), "")
            mode = mode or next((m for m in device.modes if m.lower() == "direct"), "")
        args.extend(["--device", str(device.index)])
        if mode:
            args.extend(["--mode", mode])
        if selected_color:
            args.extend(["--color", color(selected_color)])
        for index, zone_color in zones:
            args.extend(["--device", str(device.index), "--zone", str(index),
                         "--color", color(zone_color)])
    return args
