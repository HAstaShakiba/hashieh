import sys
import unittest

from gi.repository import Gio

from hashieh import shortcuts


class AcceleratorTests(unittest.TestCase):
    def test_parse_and_same(self):
        self.assertTrue(shortcuts.same("<Super>n", "<Super>N"))
        self.assertTrue(shortcuts.same("<Ctrl>f", "<Control>f"))
        self.assertEqual(shortcuts.parse("<Primary>n"),
                         shortcuts.parse("<Meta>n" if sys.platform == "darwin" else "<Control>n"))
        self.assertFalse(shortcuts.same("<Control>f", "<Control><Shift>f"))
        self.assertFalse(shortcuts.same("", "<Control>f"))

    def test_validate(self):
        self.assertIsNone(shortcuts.validate("<Super>n"))
        self.assertIsNone(shortcuts.validate("F9"))
        self.assertIsNone(shortcuts.validate(""))
        self.assertIsNotNone(shortcuts.validate("n"))            # would block typing
        self.assertIsNotNone(shortcuts.validate("<Control>"))    # no key

    def test_app_conflicts(self):
        search = shortcuts.DEFAULT_APP["search"]
        found = shortcuts.find_conflicts(search, app_shortcuts=dict(shortcuts.DEFAULT_APP))
        self.assertTrue(any(c.schema_id == "app" and c.key == "search" for c in found))
        mapping = dict(shortcuts.DEFAULT_APP)
        for c in found:
            if c.schema_id == "app":
                shortcuts.free(c, search, mapping)
        self.assertEqual(mapping["search"], "")


class NativeBackendTests(unittest.TestCase):
    """The Windows/macOS backend, driven with a fake hotkey manager on any platform."""

    class FakeHotkeys:
        def __init__(self, taken=()):
            self.registered, self.taken = {}, set(taken)

        def register(self, action, accel):
            if accel in self.taken:
                return False
            self.registered[action] = accel
            return True

        def unregister(self, action):
            self.registered.pop(action, None)

        def unregister_all(self):
            self.registered.clear()

        def can_register(self, accel):
            return accel not in self.taken and accel not in self.registered.values()

    def setUp(self):
        import tempfile
        from pathlib import Path
        from hashieh import paths
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (shortcuts._NATIVE, dict(shortcuts._runtime), paths.CONFIG_PATH, paths.CONFIG_DIR)
        paths.CONFIG_DIR = Path(self.tmp.name)
        paths.CONFIG_PATH = Path(self.tmp.name) / "config.json"
        shortcuts._NATIVE = True
        self.cfg = dict(paths.DEFAULT_CONFIG, global_shortcuts={"toggle": "<Control><Alt>n"})
        self.hotkeys = self.FakeHotkeys(taken={"<Control><Alt>t"})
        shortcuts.bind_runtime(self.cfg, self.hotkeys)

    def tearDown(self):
        from hashieh import paths
        shortcuts._NATIVE, runtime, paths.CONFIG_PATH, paths.CONFIG_DIR = self.saved
        shortcuts._runtime.clear()
        shortcuts._runtime.update(runtime)
        self.tmp.cleanup()

    def test_saved_shortcuts_are_registered(self):
        self.assertEqual(self.hotkeys.registered, {"toggle": "<Control><Alt>n"})
        self.assertEqual(shortcuts.get_global("toggle"), "<Control><Alt>n")

    def test_set_and_clear(self):
        self.assertTrue(shortcuts.set_global("new_note", "<Control><Alt>c"))
        self.assertEqual(self.hotkeys.registered["new_note"], "<Control><Alt>c")
        self.assertEqual(self.cfg["global_shortcuts"]["new_note"], "<Control><Alt>c")
        self.assertTrue(shortcuts.set_global("new_note", ""))
        self.assertNotIn("new_note", self.hotkeys.registered)

    def test_conflicts(self):
        # owned by another program: can't be freed
        found = shortcuts.find_conflicts("<Control><Alt>t", ignore_action="new_note")
        self.assertEqual([c.schema_id for c in found], ["os"])
        with self.assertRaises(ValueError):
            shortcuts.free(found[0], "<Control><Alt>t")
        # used by our other global shortcut: can be moved
        found = shortcuts.find_conflicts("<Control><Alt>n", ignore_action="new_note")
        self.assertEqual([c.schema_id for c in found], ["app-global"])
        shortcuts.free(found[0], "<Control><Alt>n")
        self.assertEqual(shortcuts.get_global("toggle"), "")
        # re-saving our own current key isn't a conflict
        shortcuts.set_global("toggle", "<Control><Alt>n")
        self.assertEqual(shortcuts.find_conflicts("<Control><Alt>n", ignore_action="toggle"), [])

    def test_suspend_restore(self):
        saved = shortcuts.suspend_globals()
        self.assertEqual(self.hotkeys.registered, {})
        shortcuts.restore_globals(saved)
        self.assertEqual(self.hotkeys.registered, {"toggle": "<Control><Alt>n"})

    def test_defaults_skip_taken_key(self):
        shortcuts.set_global("toggle", "")
        old = dict(shortcuts.DEFAULT_GLOBAL)
        try:
            shortcuts.DEFAULT_GLOBAL["toggle"] = "<Control><Alt>t"   # taken by "another program"
            self.assertEqual(shortcuts.apply_defaults(), [])
            shortcuts.DEFAULT_GLOBAL["toggle"] = "<Control><Alt>h"
            self.assertEqual(shortcuts.apply_defaults(), ["toggle"])
        finally:
            shortcuts.DEFAULT_GLOBAL.clear()
            shortcuts.DEFAULT_GLOBAL.update(old)


@unittest.skipUnless(not shortcuts._NATIVE and shortcuts._gnome_global_available(),
                     "GNOME media-keys schema not installed")
class GlobalShortcutTests(unittest.TestCase):
    """Runs against the in-memory GSettings backend set up in tests/__init__.py."""

    def test_set_get_clear(self):
        shortcuts.set_global("toggle", "<Super>j")
        self.assertEqual(shortcuts.get_global("toggle"), "<Super>j")
        media = Gio.Settings.new(shortcuts.MEDIA_KEYS)
        self.assertIn(shortcuts._path("toggle"), media.get_strv("custom-keybindings"))
        shortcuts.set_global("toggle", "")
        self.assertEqual(shortcuts.get_global("toggle"), "")
        self.assertNotIn(shortcuts._path("toggle"), media.get_strv("custom-keybindings"))

    def test_conflict_with_gnome_binding_and_free(self):
        shell = "org.gnome.shell.keybindings"
        source = Gio.SettingsSchemaSource.get_default()
        if not source.lookup(shell, True):
            self.skipTest("gnome-shell schema not installed")
        settings = Gio.Settings.new(shell)
        settings.set_strv("focus-active-notification", ["<Super>n"])
        found = [c for c in shortcuts.find_conflicts("<Super>n") if c.key == "focus-active-notification"]
        self.assertEqual(len(found), 1)
        shortcuts.free(found[0], "<Super>n")
        self.assertEqual(settings.get_strv("focus-active-notification"), [])

    def test_defaults_bind_only_free_unset_keys(self):
        default = shortcuts.DEFAULT_GLOBAL["toggle"]
        shortcuts.set_global("toggle", "")
        self.assertEqual(shortcuts.apply_defaults(), ["toggle"])
        self.assertEqual(shortcuts.get_global("toggle"), default)
        # an existing choice is kept
        shortcuts.set_global("toggle", "<Super>k")
        self.assertEqual(shortcuts.apply_defaults(), [])
        self.assertEqual(shortcuts.get_global("toggle"), "<Super>k")
        # a key used by another program is not taken
        shortcuts.set_global("toggle", "")
        other = shortcuts.CUSTOM_ROOT + "someone-else/"
        media = Gio.Settings.new(shortcuts.MEDIA_KEYS)
        media.set_strv("custom-keybindings", list(media.get_strv("custom-keybindings")) + [other])
        shortcuts._custom(other).set_string("binding", default)
        self.assertEqual(shortcuts.apply_defaults(), [])
        self.assertEqual(shortcuts.get_global("toggle"), "")
        shortcuts._custom(other).set_string("binding", "")
        media.set_strv("custom-keybindings", [p for p in media.get_strv("custom-keybindings") if p != other])

    def test_first_run_setup_runs_once(self):
        import tempfile
        from pathlib import Path
        from hashieh import paths
        with tempfile.TemporaryDirectory() as tmp:
            old_autostart, old_config = paths.AUTOSTART_PATH, paths.CONFIG_PATH
            paths.AUTOSTART_PATH = Path(tmp) / "autostart" / "hashieh.desktop"
            paths.CONFIG_PATH = Path(tmp) / "config.json"
            old_dir, paths.CONFIG_DIR = paths.CONFIG_DIR, Path(tmp)
            try:
                shortcuts.set_global("toggle", "")
                cfg = dict(paths.DEFAULT_CONFIG)
                self.assertTrue(shortcuts.first_run_setup(cfg))
                self.assertTrue(paths.autostart_enabled())
                self.assertIn("--hide", paths.AUTOSTART_PATH.read_text())
                self.assertEqual(shortcuts.get_global("toggle"), shortcuts.DEFAULT_GLOBAL["toggle"])
                # the user turns both off: a later start must not turn them back on
                paths.set_autostart(False, "")
                shortcuts.set_global("toggle", "")
                self.assertFalse(shortcuts.first_run_setup(paths.load_config()))
                self.assertFalse(paths.autostart_enabled())
                self.assertEqual(shortcuts.get_global("toggle"), "")
            finally:
                paths.AUTOSTART_PATH, paths.CONFIG_PATH, paths.CONFIG_DIR = old_autostart, old_config, old_dir

    def test_suspend_and_restore(self):
        shortcuts.set_global("toggle", "<Super>k")
        saved = shortcuts.suspend_globals()
        self.assertEqual(shortcuts.get_global("toggle"), "")
        shortcuts.restore_globals(saved)
        self.assertEqual(shortcuts.get_global("toggle"), "<Super>k")
        shortcuts.set_global("toggle", "")


if __name__ == "__main__":
    unittest.main()
