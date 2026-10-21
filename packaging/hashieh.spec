# PyInstaller spec for the Windows and macOS builds:  pyinstaller --noconfirm packaging/hashieh.spec
# GTK, its typelibs, the Adwaita icons and the pixbuf loaders are collected by PyInstaller's gi hooks.
import re
import sys
from pathlib import Path

root = Path(SPECPATH).parent
version = re.search(r'__version__ = "(.+?)"', (root / "hashieh/__init__.py").read_text()).group(1)
is_mac = sys.platform == "darwin"
is_windows = sys.platform == "win32"

a = Analysis(
    [str(root / "packaging/entry.py")],
    pathex=[str(root)],
    hiddenimports=["hashieh.ui", "hashieh.hotkeys", "hashieh.ipc"],
    hooksconfig={
        "gi": {
            "icons": ["Adwaita", "hicolor"],
            "themes": ["Adwaita"],
            "languages": ["en_US"],
            "module-versions": {"Gtk": "3.0", "Gdk": "3.0", "GdkPixbuf": "2.0"},
        },
    },
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = str(root / "data/icons" / ("hashieh.icns" if is_mac else "hashieh.ico"))
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Hashieh", console=False,
          icon=icon, upx=False)
executables = [gui]
if is_windows:
    # Same program with a console, for the command line (import/export/shortcut/selftest).
    executables.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="hashieh-cli",
                           console=True, icon=icon, upx=False))

coll = COLLECT(*executables, a.binaries, a.datas, name="Hashieh", upx=False)

if is_mac:
    app = BUNDLE(
        coll,
        name="Hashieh.app",
        icon=icon,
        bundle_identifier="io.github.hastashakiba.Hashieh",
        version=version,
        info_plist={
            "CFBundleName": "Hashieh",
            "CFBundleDisplayName": "Hashieh",
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            # A panel app: no Dock icon or menu bar, like SideNotes.
            "LSUIElement": True,
            "NSHumanReadableCopyright": "MIT License",
        },
    )
