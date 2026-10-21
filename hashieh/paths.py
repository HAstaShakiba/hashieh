"""XDG locations used by the client."""

import json
import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "hashieh"
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "hashieh"
DB_PATH = DATA_DIR / "notes.db"

# Locations used before the project was renamed from sidenotes-linux.
_LEGACY_DATA = _xdg("XDG_DATA_HOME", ".local/share") / "sidenotes-linux"
_LEGACY_CONFIG = _xdg("XDG_CONFIG_HOME", ".config") / "sidenotes-linux"
THEMES_DIR = DATA_DIR / "themes"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "side": "right",          # right | left
    "width": 360,
    "dock": True,             # pin the window to the screen edge (X11/XWayland only)
    "keep_above": True,
    "show_handle": True,      # small bar on the screen edge while the panel is hidden
    "theme": "Default",
    "appearance": "auto",     # auto | light | dark
    "font_size": 11,
    "default_formatting": "markdown",
    "last_folder": None,
    "shortcuts": {},          # in-app shortcut overrides, see shortcuts.DEFAULT_APP
    "defaults_applied": False,  # autostart + default global shortcut set up on first run
}


AUTOSTART_PATH = _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / "io.github.hastashakiba.Hashieh.desktop"


def autostart_enabled() -> bool:
    return AUTOSTART_PATH.exists()


def set_autostart(enabled: bool, launcher: str) -> None:
    if not enabled:
        AUTOSTART_PATH.unlink(missing_ok=True)
        return
    AUTOSTART_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART_PATH.write_text(
        "[Desktop Entry]\nType=Application\nName=Hashieh\nComment=Start Hashieh hidden at login\n"
        f"Exec={launcher} --hide\nIcon=io.github.hastashakiba.Hashieh\nTerminal=false\n"
        "X-GNOME-Autostart-enabled=true\n")


def migrate_legacy() -> None:
    """Move notes and settings from the old sidenotes-linux folders, once."""
    if _LEGACY_DATA.is_dir() and not DATA_DIR.exists():
        _LEGACY_DATA.rename(DATA_DIR)
        for suffix in ("", "-wal", "-shm"):
            old = DATA_DIR / ("sidenotes.db" + suffix)
            if old.exists():
                old.rename(DATA_DIR / ("notes.db" + suffix))
    if _LEGACY_CONFIG.is_dir() and not CONFIG_DIR.exists():
        _LEGACY_CONFIG.rename(CONFIG_DIR)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG_PATH)
