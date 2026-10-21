"""Per-platform locations, settings file and start-at-login."""

import json
import os
import plistlib
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = not (IS_WINDOWS or IS_MAC)

APP_ID = "io.github.hastashakiba.Hashieh"


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


if IS_WINDOWS:
    _local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    _roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    DATA_DIR = Path(os.environ.get("HASHIEH_DATA_DIR") or _local / "Hashieh")
    CONFIG_DIR = Path(os.environ.get("HASHIEH_CONFIG_DIR") or _roaming / "Hashieh")
elif IS_MAC:
    _support = Path.home() / "Library/Application Support/Hashieh"
    DATA_DIR = Path(os.environ.get("HASHIEH_DATA_DIR") or _support)
    CONFIG_DIR = Path(os.environ.get("HASHIEH_CONFIG_DIR") or _support)
else:
    DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "hashieh"
    CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "hashieh"

DB_PATH = DATA_DIR / "notes.db"
THEMES_DIR = DATA_DIR / "themes"
CONFIG_PATH = CONFIG_DIR / "config.json"

# Linux locations used before the project was renamed from sidenotes-linux.
_LEGACY_DATA = _xdg("XDG_DATA_HOME", ".local/share") / "sidenotes-linux"
_LEGACY_CONFIG = _xdg("XDG_CONFIG_HOME", ".config") / "sidenotes-linux"

DEFAULT_CONFIG = {
    "side": "right",          # right | left
    "width": 360,
    "dock": True,             # pin the window to the screen edge
    "keep_above": True,
    "show_handle": True,      # small bar on the screen edge while the panel is hidden
    "theme": "Default",
    "appearance": "auto",     # auto | light | dark
    "font_size": 11,
    "default_formatting": "markdown",
    "last_folder": None,
    "shortcuts": {},          # in-app shortcut overrides, see shortcuts.DEFAULT_APP
    "global_shortcuts": {},   # Windows/macOS global shortcuts (Linux keeps them in GNOME)
    "defaults_applied": False,  # autostart + default global shortcut set up on first run
}


# Start at login -------------------------------------------------------------------------------
# Linux: XDG autostart entry. macOS: a LaunchAgent. Windows: the per-user Run registry key.

if IS_MAC:
    AUTOSTART_PATH = Path.home() / "Library/LaunchAgents" / f"{APP_ID}.plist"
else:
    AUTOSTART_PATH = _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / f"{APP_ID}.desktop"

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_enabled() -> bool:
    if IS_WINDOWS:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, "Hashieh")
            return True
        except OSError:
            return False
    return AUTOSTART_PATH.exists()


def set_autostart(enabled: bool, launcher: str) -> None:
    if IS_WINDOWS:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, "Hashieh", 0, winreg.REG_SZ, f'"{launcher}" --hide')
            else:
                try:
                    winreg.DeleteValue(key, "Hashieh")
                except OSError:
                    pass
        return
    if not enabled:
        AUTOSTART_PATH.unlink(missing_ok=True)
        return
    AUTOSTART_PATH.parent.mkdir(parents=True, exist_ok=True)
    if IS_MAC:
        AUTOSTART_PATH.write_bytes(plistlib.dumps({
            "Label": APP_ID,
            "ProgramArguments": [launcher, "--hide"],
            "RunAtLoad": True,
            "ProcessType": "Interactive",
        }))
        return
    AUTOSTART_PATH.write_text(
        "[Desktop Entry]\nType=Application\nName=Hashieh\nComment=Start Hashieh hidden at login\n"
        f"Exec={launcher} --hide\nIcon={APP_ID}\nTerminal=false\n"
        "X-GNOME-Autostart-enabled=true\n")


def migrate_legacy() -> None:
    """Move notes and settings from the old sidenotes-linux folders, once."""
    if not IS_LINUX:
        return
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
