import tempfile
import unittest
from pathlib import Path

from archer.apps.sway import _set_flag, _wlsunset_args
from archer.swayconfig import SwayConfig


class NightLightTest(unittest.TestCase):
    def cfg(self, text):
        path = Path(tempfile.mkdtemp()) / "config"
        path.write_text(text)
        return SwayConfig(path)

    def test_parses_wlsunset_line(self):
        line, args = _wlsunset_args(self.cfg("exec swayidle -w\nexec wlsunset -S 06:30 -t 3500\n"))
        self.assertEqual(line.body, "exec wlsunset -S 06:30 -t 3500")
        self.assertEqual(args, ["-S", "06:30", "-t", "3500"])

    def test_absent_and_unparsable(self):
        self.assertEqual(_wlsunset_args(self.cfg("exec swayidle -w\n")), (None, None))
        line, args = _wlsunset_args(self.cfg("exec wlsunset -S '07:00\n"))
        self.assertIsNotNone(line)
        self.assertIsNone(args)

    def test_set_flag(self):
        args = ["-l", "51.5", "-t", "3500"]
        self.assertEqual(_set_flag(args, "-t", "3000"), ["-l", "51.5", "-t", "3000"])
        self.assertEqual(_set_flag(args, "-l", None), ["-t", "3500"])
        self.assertEqual(_set_flag(args, "-S", "07:00"), args + ["-S", "07:00"])


if __name__ == "__main__":
    unittest.main()
