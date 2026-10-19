import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from archer.apps.openrgb import OpenRGB
from archer.configfile import KeyValueConfig
from archer.rgb import Device


class OpenRGBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "rgb.conf"
        self.app = OpenRGB()
        self.app.config_path = lambda: self.path
        self.device = Device(0, "RAM", "DRAM", "I2C: address 0x5A", modes=["Direct"], zones=["RAM"])
        self.app.devices = [self.device]
        self.cfg = KeyValueConfig(self.path)
        self.cfg.set(self.device.key + ".color", ["#abcdef"])
        self.path.write_text(self.cfg.render())

    @patch("archer.rgb.run_openrgb")
    @patch("archer.rgb.discover")
    def test_apply_resolves_current_device_index(self, discover, run):
        discover.return_value = [Device(5, "RAM", "DRAM", "I2C: address 0x5A",
                                       modes=["Direct"], zones=["RAM"])]
        self.assertEqual(self.app.apply(), "Saved and applied RGB lighting")
        run.assert_called_once_with(["--device", "5", "--mode", "Direct", "--color", "abcdef"],
                                    executable="openrgb")

    @patch("archer.rgb.run_openrgb")
    @patch("archer.rgb.discover", return_value=[])
    def test_disconnection_does_not_write_to_other_hardware(self, discover, run):
        self.assertIn("no longer detected", self.app.apply())
        run.assert_not_called()

    @patch("archer.rgb.run_openrgb")
    @patch("archer.rgb.discover")
    def test_changed_zone_order_prevents_writes(self, discover, run):
        discover.return_value = [Device(0, "RAM", "DRAM", "I2C: address 0x5A",
                                       modes=["Direct"], zones=["Other zone"])]
        self.assertIn("zones changed", self.app.apply())
        run.assert_not_called()

    def test_validation_rejects_invalid_colors_without_hardware_access(self):
        self.path.write_text("all.color = red\n")
        with patch("archer.rgb.run_openrgb") as run:
            self.assertIn("six hex digits", self.app.validate(self.path, self.path))
            run.assert_not_called()

    @patch("archer.rgb.run_openrgb")
    @patch("archer.rgb.discover")
    def test_custom_executable_used_for_both_discovery_and_apply(self, discover, run):
        executable = str(Path(self.tmp.name) / "OpenRGB 1.1.AppImage")
        self.cfg.set("openrgb.executable", [executable])
        self.path.write_text(self.cfg.render())
        discover.return_value = [self.device]
        self.app.load_options(self.cfg)
        self.assertEqual(self.app.apply(), "Saved and applied RGB lighting")
        self.assertEqual(discover.call_count, 2)
        discover.assert_called_with(executable=executable)
        self.assertEqual(run.call_args.kwargs["executable"], executable)

    def test_validation_rejects_missing_executable(self):
        self.cfg.set("openrgb.executable", [str(Path(self.tmp.name) / "missing")])
        self.path.write_text(self.cfg.render())
        self.assertIn("not found", self.app.validate(self.path, self.path))

    def test_available_accepts_appimage_without_system_openrgb(self):
        executable = str(Path(self.tmp.name) / "OpenRGB.AppImage")
        self.cfg.set("openrgb.executable", [executable])
        self.path.write_text(self.cfg.render())
        with patch("archer.apps.openrgb.shutil.which", return_value=executable) as which:
            self.assertTrue(self.app.available())
            which.assert_called_once_with(executable)

    def test_executable_defaults_and_expands_home(self):
        self.assertEqual(self.app.executable(self.cfg), "openrgb")
        self.cfg.set("openrgb.executable", ["~/Applications/OpenRGB.AppImage"])
        self.assertEqual(self.app.executable(self.cfg), str(Path.home() / "Applications/OpenRGB.AppImage"))

    @patch("archer.rgb.discover", side_effect=ValueError("Access denied"))
    def test_discovery_failure_keeps_rgb_section_available(self, discover):
        options = self.app.load_options(self.cfg)
        self.assertEqual(self.app.error, "Access denied")
        self.assertTrue(any(option.key == "all.color" for option in options))
        self.assertTrue(any(option.key == self.device.key + ".color" for option in options))


if __name__ == "__main__":
    unittest.main()
