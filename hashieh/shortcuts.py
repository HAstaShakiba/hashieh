"""Keyboard shortcuts.

Two kinds, mirroring the macOS app's toggleShortcut/newNoteShortcut plus its
in-app keys:

* Global shortcuts work anywhere on the desktop. GTK apps can't grab keys
  themselves on GNOME, so they are stored as GNOME custom keybindings that run
  the launcher (`hashieh --toggle` and so on).
* In-app shortcuts only work while the panel has focus; they live in config.json.

Accelerators use GTK syntax, e.g. "<Super>n" or "<Control><Shift>f".
"""

import shutil
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

DEFAULT_APP = {
    "new_note": "<Control>n",
    "search": "<Control>f",
    "hide": "<Control>w",
    "delete_note": "<Control>Delete",
    "zoom_in": "<Control>equal",
    "zoom_out": "<Control>minus",
    "quit": "<Control>q",
}

# Keys that may be used without a modifier.
_BARE_OK = {f"F{i}" for i in range(1, 25)}


class Conflict(NamedTuple):
    description: str
    schema_id: str
    path: Optional[str]
    key: str


def launcher() -> str:
    installed = Path.home() / ".local/bin/hashieh"
    if installed.exists():
        return str(installed)
    return shutil.which("hashieh") or str(Path(__file__).resolve().parents[1] / "bin" / "hashieh")


def parse(accel: str):
    """(lower-case keyval, modifiers) or None."""
    if not accel:
        return None
    key, mods = Gtk.accelerator_parse(accel)
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
    key, mods = Gtk.accelerator_parse(accel)
    return Gtk.accelerator_get_label(key, mods)


def validate(accel: str) -> Optional[str]:
    """Return an error message, or None if the accelerator is usable."""
    if not accel:
        return None
    key, mods = Gtk.accelerator_parse(accel)
    if not key:
        return f"“{accel}” is not a valid shortcut. Example: <Super>n or <Control><Alt>s"
    name = Gdk.keyval_name(key) or ""
    if not (mods & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK |
                    Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK)) and name not in _BARE_OK:
        return "Use Ctrl, Alt or Super with the key, so normal typing isn't blocked."
    return None


# GNOME global shortcuts -----------------------------------------------------------------------

def _schema(schema_id: str):
    source = Gio.SettingsSchemaSource.get_default()
    return source.lookup(schema_id, True) if source else None


def global_available() -> bool:
    media = _schema(MEDIA_KEYS)
    return bool(media and media.has_key("custom-keybindings") and _schema(CUSTOM_SCHEMA))


def _custom(path: str) -> Gio.Settings:
    return Gio.Settings.new_with_path(CUSTOM_SCHEMA, path)


def _path(action: str) -> str:
    return CUSTOM_ROOT + GLOBAL_ACTIONS[action][1] + "/"


def get_global(action: str) -> str:
    if not global_available():
        return ""
    media = Gio.Settings.new(MEDIA_KEYS)
    path = _path(action)
    if path not in media.get_strv("custom-keybindings"):
        return ""
    return _custom(path).get_string("binding")


def set_global(action: str, accel: str) -> None:
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


def suspend_globals() -> dict:
    """Clear our global bindings while recording a new one, so pressing the old key
    records it instead of toggling the panel. Returns what restore_globals needs."""
    saved = {}
    if global_available():
        for action in GLOBAL_ACTIONS:
            saved[action] = get_global(action)
            if saved[action]:
                _custom(_path(action)).set_string("binding", "")
        Gio.Settings.sync()
    return saved


def restore_globals(saved: dict) -> None:
    for action, accel in saved.items():
        if accel:
            _custom(_path(action)).set_string("binding", accel)
    Gio.Settings.sync()


def find_conflicts(accel: str, ignore_action: Optional[str] = None,
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
        if global_available():
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


def free(conflict: Conflict, accel: str, app_shortcuts: Optional[dict] = None) -> None:
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


def migrate_legacy() -> None:
    """Move global shortcuts saved under the old sidenotes-linux names to the new ones."""
    if not global_available():
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
        if accel and not get_global(action):
            set_global(action, accel)
    Gio.Settings.sync()
