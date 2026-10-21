"""Keyboard shortcuts.

Two kinds, mirroring the macOS app's toggleShortcut/newNoteShortcut plus its
in-app keys:

* Global shortcuts work anywhere on the desktop.
  - Linux/GNOME: GTK apps can't grab keys themselves, so they are stored as GNOME
    custom keybindings that run the launcher (`hashieh --toggle` and so on).
  - Windows/macOS: the running app registers them with the system (hotkeys.py);
    they are saved in config.json.
* In-app shortcuts only work while the panel has focus; they live in config.json.

Accelerators use GTK syntax, e.g. "<Super>n" or "<Control><Shift>f".
"""

import shutil
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = MEDIA_KEYS + ".custom-keybinding"
CUSTOM_ROOT = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"

# action -> (label, keybinding path name, launcher arguments)
GLOBAL_ACTIONS = {
    "toggle": ("Show / hide Hashieh", "hashieh", "--toggle"),
    "new_note": ("New note from clipboard", "hashieh-new-note", "--new-note-from-clipboard"),
}

# Keybinding paths used before the project was renamed from sidenotes-linux.
LEGACY_PATHS = {
    "toggle": "sidenotes-linux",
    "new_note": "sidenotes-linux-new-note",
}

APP_ACTIONS = {
    "new_note": "New note / folder",
    "search": "Search",
    "hide": "Hide panel",
    "delete_note": "Delete current note",
    "zoom_in": "Bigger text",
    "zoom_out": "Smaller text",
    "quit": "Quit",
}

# Global shortcuts set up on first run, if the key isn't used by anything else.
# Win+Ctrl+N is taken by Windows (Narrator settings), so Windows uses Ctrl+Alt+N.
DEFAULT_GLOBAL = {
    "toggle": "<Control><Alt>n" if sys.platform == "win32" else "<Super><Control>n",
}

_NATIVE = sys.platform in ("win32", "darwin")

# <Primary> is Ctrl on Linux/Windows and Command on macOS.
DEFAULT_APP = {
    "new_note": "<Primary>n",
    "search": "<Primary>f",
    "hide": "<Primary>w",
    "delete_note": "<Primary>Delete",
    "zoom_in": "<Primary>equal",
    "zoom_out": "<Primary>minus",
    "quit": "<Primary>q",
}

# Keys that may be used without a modifier.
_BARE_OK = {f"F{i}" for i in range(1, 25)}


class Conflict(NamedTuple):
    description: str
    schema_id: str
    path: Optional[str]
    key: str


def launcher() -> str:
    if getattr(sys, "frozen", False):  # PyInstaller build on Windows/macOS
        return sys.executable
    installed = Path.home() / ".local/bin/hashieh"
    if installed.exists():
        return str(installed)
    return shutil.which("hashieh") or str(Path(__file__).resolve().parents[1] / "bin" / "hashieh")


def expand(accel: str) -> str:
    """Resolve <Primary> ourselves (Command on macOS, Ctrl elsewhere): GTK can only do
    it with a display, and the answer must not change between machines."""
    return accel.replace("<Primary>", "<Meta>" if sys.platform == "darwin" else "<Control>")


def parse(accel: str):
    """(lower-case keyval, modifiers) or None."""
    if not accel:
        return None
    key, mods = Gtk.accelerator_parse(expand(accel))
    if not key:
        return None
    return Gdk.keyval_to_lower(key), int(mods)


def same(a: str, b: str) -> bool:
    pa, pb = parse(a), parse(b)
    return pa is not None and pa == pb


def label(accel: str) -> str:
    parsed = parse(accel)
    if not parsed:
        return "Disabled"
    key, mods = Gtk.accelerator_parse(expand(accel))
    return Gtk.accelerator_get_label(key, mods)


def validate(accel: str) -> Optional[str]:
    """Return an error message, or None if the accelerator is usable."""
    if not accel:
        return None
    key, mods = Gtk.accelerator_parse(expand(accel))
    if not key:
        return f"“{accel}” is not a valid shortcut. Example: <Super>n or <Control><Alt>s"
    name = Gdk.keyval_name(key) or ""
    if not (mods & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK |
                    Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK |
                    Gdk.ModifierType.META_MASK)) and name not in _BARE_OK:
        return "Use Ctrl, Alt or Super with the key, so normal typing isn't blocked."
    return None


# GNOME global shortcuts -----------------------------------------------------------------------

def _schema(schema_id: str):
    source = Gio.SettingsSchemaSource.get_default()
    return source.lookup(schema_id, True) if source else None


def _gnome_global_available() -> bool:
    media = _schema(MEDIA_KEYS)
    return bool(media and media.has_key("custom-keybindings") and _schema(CUSTOM_SCHEMA))


def _custom(path: str) -> Gio.Settings:
    return Gio.Settings.new_with_path(CUSTOM_SCHEMA, path)


def _path(action: str) -> str:
    return CUSTOM_ROOT + GLOBAL_ACTIONS[action][1] + "/"


def _gnome_get_global(action: str) -> str:
    if not _gnome_global_available():
        return ""
    media = Gio.Settings.new(MEDIA_KEYS)
    path = _path(action)
    if path not in media.get_strv("custom-keybindings"):
        return ""
    return _custom(path).get_string("binding")


def _gnome_set_global(action: str, accel: str) -> None:
    media = Gio.Settings.new(MEDIA_KEYS)
    path = _path(action)
    paths = list(media.get_strv("custom-keybindings"))
    custom = _custom(path)
    if accel:
        title, _, args = GLOBAL_ACTIONS[action]
        custom.set_string("name", f"Hashieh: {title}")
        custom.set_string("command", f"{launcher()} {args}")
        custom.set_string("binding", accel)
        if path not in paths:
            media.set_strv("custom-keybindings", paths + [path])
    else:
        custom.set_string("binding", "")
        if path in paths:
            media.set_strv("custom-keybindings", [p for p in paths if p != path])
    Gio.Settings.sync()


def _gnome_suspend_globals() -> dict:
    """Clear our global bindings while recording a new one, so pressing the old key
    records it instead of toggling the panel. Returns what restore_globals needs."""
    saved = {}
    if _gnome_global_available():
        for action in GLOBAL_ACTIONS:
            saved[action] = _gnome_get_global(action)
            if saved[action]:
                _custom(_path(action)).set_string("binding", "")
        Gio.Settings.sync()
    return saved


def _gnome_restore_globals(saved: dict) -> None:
    for action, accel in saved.items():
        if accel:
            _custom(_path(action)).set_string("binding", accel)
    Gio.Settings.sync()


def _gnome_find_conflicts(accel: str, ignore_action: Optional[str] = None,
                   app_shortcuts: Optional[dict] = None) -> List[Conflict]:
    """Other desktop or Hashieh shortcuts that already use `accel`."""
    if not accel:
        return []
    found: List[Conflict] = []
    source = Gio.SettingsSchemaSource.get_default()
    if source:
        non_relocatable, _ = source.list_schemas(True)
        for schema_id in non_relocatable:
            if "keybindings" not in schema_id and schema_id != MEDIA_KEYS:
                continue
            schema = source.lookup(schema_id, True)
            settings = Gio.Settings.new(schema_id)
            for key in schema.list_keys():
                if key == "custom-keybindings":
                    continue
                value = settings.get_value(key)
                kind = value.get_type_string()
                values = value.unpack() if kind == "as" else [value.unpack()] if kind == "s" else []
                if any(isinstance(v, str) and same(v, accel) for v in values):
                    summary = schema.get_key(key).get_summary() or key
                    found.append(Conflict(f"GNOME: {summary}", schema_id, None, key))
        if _gnome_global_available():
            ours = {_path(a) for a in GLOBAL_ACTIONS if a == ignore_action}
            for path in Gio.Settings.new(MEDIA_KEYS).get_strv("custom-keybindings"):
                if path in ours:
                    continue
                custom = _custom(path)
                if same(custom.get_string("binding"), accel):
                    found.append(Conflict(f"Custom shortcut: {custom.get_string('name') or path}",
                                          CUSTOM_SCHEMA, path, "binding"))
    for action, value in (app_shortcuts or {}).items():
        if same(value, accel):
            found.append(Conflict(f"Hashieh: {APP_ACTIONS.get(action, action)}", "app", None, action))
    return found


def _gnome_free(conflict: Conflict, accel: str, app_shortcuts: Optional[dict] = None) -> None:
    """Remove `accel` from the conflicting binding."""
    if conflict.schema_id == "app":
        if app_shortcuts is not None:
            app_shortcuts[conflict.key] = ""
        return
    settings = _custom(conflict.path) if conflict.path else Gio.Settings.new(conflict.schema_id)
    value = settings.get_value(conflict.key)
    if value.get_type_string() == "as":
        settings.set_strv(conflict.key, [v for v in value.unpack() if not same(v, accel)])
    else:
        settings.set_string(conflict.key, "")
    Gio.Settings.sync()


# Windows / macOS global shortcuts ---------------------------------------------------------------
# Saved in config.json and registered by the running app through hotkeys.py.

_runtime = {"cfg": None, "hotkeys": None}


def bind_runtime(cfg: dict, hotkey_manager) -> None:
    """Called by the app: use its live config and register the saved global shortcuts."""
    _runtime.update(cfg=cfg, hotkeys=hotkey_manager)
    if hotkey_manager:
        for action, accel in cfg.get("global_shortcuts", {}).items():
            if accel and action in GLOBAL_ACTIONS:
                hotkey_manager.register(action, accel)


def _native_cfg() -> dict:
    from .paths import load_config
    return _runtime["cfg"] if _runtime["cfg"] is not None else load_config()


def _native_get_global(action: str) -> str:
    return _native_cfg().get("global_shortcuts", {}).get(action, "")


def _native_set_global(action: str, accel: str) -> bool:
    from .paths import save_config
    cfg = _native_cfg()
    saved = dict(cfg.get("global_shortcuts", {}))
    if accel:
        saved[action] = accel
    else:
        saved.pop(action, None)
    cfg["global_shortcuts"] = saved
    save_config(cfg)
    manager = _runtime["hotkeys"]
    if manager is None:
        return True
    if not accel:
        manager.unregister(action)
        return True
    return manager.register(action, accel)


def _native_find_conflicts(accel: str, ignore_action: Optional[str]) -> List[Conflict]:
    found = []
    for action, (title, _, _) in GLOBAL_ACTIONS.items():
        if action != ignore_action and same(_native_get_global(action), accel):
            found.append(Conflict(f"Hashieh: {title}", "app-global", None, action))
    manager = _runtime["hotkeys"]
    ours = _native_get_global(ignore_action) if ignore_action else ""
    if manager is not None and not found and not same(ours, accel) and not manager.can_register(accel):
        found.append(Conflict("Another program or the system", "os", None, ""))
    return found


# Public API (dispatches to GNOME on Linux, to the in-app hotkeys elsewhere) ----------------------

def global_available() -> bool:
    if _NATIVE:
        from . import hotkeys
        return _runtime["hotkeys"] is not None or hotkeys.supported()
    return _gnome_global_available()


def global_scope() -> str:
    """Where global shortcuts work, for the settings dialog."""
    if _NATIVE:
        return "Work anywhere on your computer while Hashieh is running (it starts at login)."
    return "Work anywhere on the desktop (saved as GNOME keyboard shortcuts)."


def get_global(action: str) -> str:
    return _native_get_global(action) if _NATIVE else _gnome_get_global(action)


def set_global(action: str, accel: str) -> bool:
    """Save a global shortcut. False if the system refused to register it."""
    if _NATIVE:
        return _native_set_global(action, accel)
    _gnome_set_global(action, accel)
    return True


def suspend_globals() -> dict:
    """Stop our global shortcuts while recording a new one, so pressing the old key
    records it instead of toggling the panel. Returns what restore_globals needs."""
    if not _NATIVE:
        return _gnome_suspend_globals()
    saved = {action: _native_get_global(action) for action in GLOBAL_ACTIONS}
    if _runtime["hotkeys"] is not None:
        _runtime["hotkeys"].unregister_all()
    return saved


def restore_globals(saved: dict) -> None:
    if not _NATIVE:
        _gnome_restore_globals(saved)
        return
    manager = _runtime["hotkeys"]
    if manager is not None:
        for action, accel in saved.items():
            if accel:
                manager.register(action, accel)


def find_conflicts(accel: str, ignore_action: Optional[str] = None,
                   app_shortcuts: Optional[dict] = None) -> List[Conflict]:
    """Other desktop or Hashieh shortcuts that already use `accel`.

    Conflicts with schema_id "os" can't be freed: another program owns the key."""
    if not accel:
        return []
    if _NATIVE:
        found = _native_find_conflicts(accel, ignore_action)
        for action, value in (app_shortcuts or {}).items():
            if same(value, accel):
                found.append(Conflict(f"Hashieh: {APP_ACTIONS.get(action, action)}", "app", None, action))
        return found
    return _gnome_find_conflicts(accel, ignore_action, app_shortcuts)


def free(conflict: Conflict, accel: str, app_shortcuts: Optional[dict] = None) -> None:
    """Remove `accel` from the conflicting binding."""
    if conflict.schema_id == "app-global":
        set_global(conflict.key, "")
    elif conflict.schema_id == "os":
        raise ValueError("A shortcut owned by another program can't be freed from here.")
    else:
        _gnome_free(conflict, accel, app_shortcuts)


def migrate_legacy() -> None:
    """Move global shortcuts saved under the old sidenotes-linux names to the new ones."""
    if _NATIVE or not _gnome_global_available():
        return
    media = Gio.Settings.new(MEDIA_KEYS)
    for action, old_name in LEGACY_PATHS.items():
        old = CUSTOM_ROOT + old_name + "/"
        paths = list(media.get_strv("custom-keybindings"))
        if old not in paths:
            continue
        accel = _custom(old).get_string("binding")
        for key in ("binding", "command", "name"):
            _custom(old).reset(key)
        media.set_strv("custom-keybindings", [p for p in paths if p != old])
        if accel and not _gnome_get_global(action):
            _gnome_set_global(action, accel)
    Gio.Settings.sync()


def apply_defaults() -> List[str]:
    """Bind DEFAULT_GLOBAL shortcuts that are unset and free. Never takes a key
    from another binding. Returns the actions that were bound."""
    bound = []
    if not global_available():
        return bound
    for action, accel in DEFAULT_GLOBAL.items():
        if get_global(action) or find_conflicts(accel, ignore_action=action):
            continue
        set_global(action, accel)
        bound.append(action)
    return bound


def first_run_setup(cfg: dict) -> bool:
    """Turn on autostart and the default global shortcut the first time the app runs.

    Runs once per user (recorded in config), so turning either off later sticks.
    Returns True if it ran.
    """
    from .paths import autostart_enabled, save_config, set_autostart

    if cfg.get("defaults_applied"):
        return False
    if not autostart_enabled():
        set_autostart(True, launcher())
    apply_defaults()
    cfg["defaults_applied"] = True
    save_config(cfg)
    return True
