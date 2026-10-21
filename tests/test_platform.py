import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from gi.repository import GLib

from hashieh import hotkeys, ipc


class KeyMappingTests(unittest.TestCase):
    def test_windows_virtual_keys(self):
        # MOD_CONTROL=2, MOD_ALT=1, MOD_SHIFT=4, MOD_WIN=8
        self.assertEqual(hotkeys._windows_key("<Control><Alt>n"), (0x3, ord("N")))
        self.assertEqual(hotkeys._windows_key("<Super><Shift>F9"), (0xC, 0x78))
        self.assertEqual(hotkeys._windows_key("<Control>space"), (0x2, 0x20))
        self.assertIsNone(hotkeys._windows_key(""))

    def test_mac_key_codes(self):
        # cmdKey=0x100, shiftKey=0x200, optionKey=0x800, controlKey=0x1000
        self.assertEqual(hotkeys._mac_key("<Super><Control>n"), (0x1100, 0x2D))
        self.assertEqual(hotkeys._mac_key("<Meta><Alt>a"), (0x0900, 0x00))
        self.assertEqual(hotkeys._mac_key("<Super><Shift>F1"), (0x0300, 0x7A))
        self.assertIsNone(hotkeys._mac_key("<Control>Multi_key"))


class InstanceHandOffTests(unittest.TestCase):
    def test_parse_argv(self):
        self.assertEqual(ipc.parse_argv(["--toggle"]), {"toggle": True})
        self.assertEqual(ipc.parse_argv(["--new-note", "hi there", "--show"]),
                         {"new-note": "hi there", "show": True})
        self.assertEqual(ipc.parse_argv(["--new-note=x"]), {"new-note": "x"})
        self.assertEqual(ipc.parse_argv(["--unknown"]), {})

    def test_forward_reaches_running_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = ipc.INSTANCE_FILE
            ipc.INSTANCE_FILE = Path(tmp) / "instance.json"
            received = []
            loop = GLib.MainLoop()
            server = ipc.Server(lambda argv: (received.append(argv), loop.quit()))
            try:
                info = json.loads(ipc.INSTANCE_FILE.read_text())
                if os.name == "posix":
                    self.assertEqual(ipc.INSTANCE_FILE.stat().st_mode & 0o777, 0o600)
                # The client blocks, so run it in a thread while the server's loop spins.
                import threading
                result = {}
                thread = threading.Thread(target=lambda: result.update(ok=ipc.forward(["--toggle"])))
                thread.start()
                GLib.timeout_add_seconds(5, loop.quit)
                loop.run()
                thread.join(5)
                self.assertTrue(result.get("ok"))
                self.assertEqual(received, [["--toggle"]])
                # A wrong token is refused.
                info["token"] = "0" * 32
                ipc.INSTANCE_FILE.write_text(json.dumps(info))
                result.clear()
                thread = threading.Thread(target=lambda: result.update(ok=ipc.forward(["--quit"])))
                thread.start()
                ctx = GLib.MainContext.default()
                while thread.is_alive():
                    ctx.iteration(False)
                self.assertFalse(result.get("ok"))
                self.assertEqual(received, [["--toggle"]])
            finally:
                server.close()
                ipc.INSTANCE_FILE = old

    def test_forward_without_instance(self):
        old = ipc.INSTANCE_FILE
        ipc.INSTANCE_FILE = Path(tempfile.gettempdir()) / "hashieh-no-such-instance.json"
        try:
            self.assertFalse(ipc.forward(["--toggle"]))
        finally:
            ipc.INSTANCE_FILE = old


if __name__ == "__main__":
    unittest.main()
