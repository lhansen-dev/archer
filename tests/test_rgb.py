import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archer import rgb
from archer.configfile import KeyValueConfig


DEVICES = """0: Corsair Vengeance RGB Pro DDR4
  Type: DRAM
  Location: I2C: SMBus, address 0x5A
  Modes: Direct [Color Shift] 'Color Pulse'
  Zones: 'Corsair DRAM'
1: Corsair Vengeance RGB Pro DDR4
  Type: DRAM
  Location: I2C: SMBus, address 0x5B
  Modes: [Direct] 'Color Shift' 'Color Pulse'
  Zones: 'Corsair DRAM'
2: Gigabyte GeForce RTX 3060 Ti Vision OC
  Type: GPU
  Location: I2C: NVIDIA i2c adapter 1 at 23:00.0
 (/dev/i2c-8), address 0x63
  Modes: [Direct] Breathing
  Zones: 'GPU Zone'
3: MSI MPG X570 GAMING PLUS (MS-7C37)
  Type: Motherboard
  Location: HID: /dev/hidraw0
  Serial: A0202109060C
  Modes: Direct [Static] 'Double flashing'
  Zones: JRGB1 JRGB2 JRAINBOW1 JRAINBOW2 ONBOARD
"""


class RGBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = KeyValueConfig(Path(self.tmp.name) / "rgb.conf")
        self.devices = rgb.parse_devices(DEVICES)

    def test_discovery_preserves_names_zones_and_active_multiword_mode(self):
        self.assertEqual(len(self.devices), 4)
        self.assertEqual(self.devices[0].modes, ["Direct", "Color Shift", "Color Pulse"])
        self.assertEqual(self.devices[0].zones, ["Corsair DRAM"])
        self.assertEqual(self.devices[3].zones[-1], "ONBOARD")
        self.assertTrue(self.devices[2].location.endswith("(/dev/i2c-8), address 0x63"))

    def test_identical_ram_names_have_distinct_stable_keys(self):
        self.assertNotEqual(self.devices[0].key, self.devices[1].key)
        key = self.devices[0].key
        self.devices[0].index = 9
        self.assertEqual(self.devices[0].key, key)
        self.cfg.set(key + ".color", ["#ff0000"])
        self.assertEqual(rgb.commands(self.devices, self.cfg),
                         ["--device", "9", "--mode", "Direct", "--color", "ff0000"])

    def test_serial_identity_survives_hidraw_renumbering(self):
        device = self.devices[-1]
        key = device.key
        device.location = "HID: /dev/hidraw9"
        self.assertEqual(key, device.key)

    def test_quoted_active_multiword_mode(self):
        devices = rgb.parse_devices(DEVICES.replace("[Color Shift]", "['Color Shift']"))
        self.assertEqual(devices[0].modes, self.devices[0].modes)

    def test_no_saved_settings_produces_no_writes(self):
        self.assertEqual(rgb.commands(self.devices, self.cfg), [])

    def test_global_color_uses_static_or_direct_per_device(self):
        self.cfg.set("all.color", ["#abcdef"])
        args = rgb.commands(self.devices, self.cfg)
        self.assertEqual(args.count("--device"), 4)
        self.assertEqual(args.count("--color"), 4)
        self.assertEqual(args[-6:], ["--device", "3", "--mode", "Static", "--color", "abcdef"])

    def test_device_and_zone_override_global_color(self):
        device = self.devices[-1]
        self.cfg.set("all.color", ["#ff0000"])
        self.cfg.set(device.key + ".color", ["00ff00"])
        self.cfg.set(device.key + ".zone-4.color", ["0000ff"])
        self.assertEqual(rgb.commands([device], self.cfg), [
            "--device", "3", "--mode", "Static", "--color", "00ff00",
            "--device", "3", "--zone", "4", "--color", "0000ff"])

    def test_explicit_effect_is_preserved_and_checked(self):
        device = self.devices[0]
        self.cfg.set(device.key + ".mode", ["Color Shift"])
        self.assertEqual(rgb.commands([device], self.cfg), ["--device", "0", "--mode", "Color Shift"])
        self.cfg.set(device.key + ".mode", ["unsupported"])
        with self.assertRaises(ValueError):
            rgb.commands([device], self.cfg)

    def test_malformed_colors_rejected(self):
        for value in ["red", "#fff", "1234567", "ff0000\n--server", "zzzzzz"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                rgb.color(value)

    def test_ambiguous_hardware_identity_rejected(self):
        with self.assertRaises(ValueError):
            rgb.parse_devices(DEVICES + DEVICES)

    @patch("archer.rgb.subprocess.run")
    def test_discovery_only_lists_devices(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = DEVICES
        self.assertEqual(len(rgb.discover()), 4)
        self.assertEqual(run.call_args.args[0], ["openrgb", "--list-detailed"])

    @patch("archer.rgb.subprocess.run")
    def test_appimage_path_with_spaces_is_a_single_argument(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = DEVICES
        executable = "/tmp/OpenRGB 1.1.AppImage"
        self.assertEqual(len(rgb.discover(executable=executable)), 4)
        self.assertEqual(run.call_args.args[0], [executable, "--list-detailed"])


if __name__ == "__main__":
    unittest.main()
