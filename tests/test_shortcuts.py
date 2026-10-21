import unittest

from gi.repository import Gio

from hashieh import shortcuts


class AcceleratorTests(unittest.TestCase):
    def test_parse_and_same(self):
        self.assertTrue(shortcuts.same("<Super>n", "<Super>N"))
        self.assertTrue(shortcuts.same("<Ctrl>f", "<Control>f"))
        self.assertFalse(shortcuts.same("<Control>f", "<Control><Shift>f"))
        self.assertFalse(shortcuts.same("", "<Control>f"))

    def test_validate(self):
        self.assertIsNone(shortcuts.validate("<Super>n"))
        self.assertIsNone(shortcuts.validate("F9"))
        self.assertIsNone(shortcuts.validate(""))
        self.assertIsNotNone(shortcuts.validate("n"))            # would block typing
        self.assertIsNotNone(shortcuts.validate("<Control>"))    # no key

    def test_app_conflicts(self):
        found = shortcuts.find_conflicts("<Control>f", app_shortcuts=dict(shortcuts.DEFAULT_APP))
        self.assertTrue(any(c.schema_id == "app" and c.key == "search" for c in found))
        mapping = dict(shortcuts.DEFAULT_APP)
        for c in found:
            if c.schema_id == "app":
                shortcuts.free(c, "<Control>f", mapping)
        self.assertEqual(mapping["search"], "")


@unittest.skipUnless(shortcuts.global_available(), "GNOME media-keys schema not installed")
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
