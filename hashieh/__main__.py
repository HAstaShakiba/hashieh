"""Entry point.

    hashieh                              start (or show) the panel
    hashieh --toggle | --show | --hide   show/hide the panel
    hashieh --new-note "text"            add a note to the current folder
    hashieh --new-note-from-clipboard
    hashieh import PATH                  import data.json / SideNotes.sqlite / .snbackup / folder
    hashieh export DIR                   export data.json + images/ to DIR
    hashieh themes install PATH          install .sntheme files (file, folder, or SideNotes.app)
    hashieh themes list
    hashieh shortcut list
    hashieh shortcut toggle|new_note ACCEL [--force]
                                         set a global GNOME shortcut, e.g. '<Super><Control>n';
                                         an empty ACCEL removes it, --force takes the key over
"""

import os
import sys

from . import __version__
from .paths import DB_PATH, load_config, migrate_legacy


def _cli(argv) -> int:
    from . import interop, theme
    from .model import Store

    cmd = argv[0]
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "--version":
        print(__version__)
        return 0
    if cmd == "import" and len(argv) == 2:
        store = Store(DB_PATH)
        try:
            print(f"Imported {interop.import_any(store, argv[1])}.")
        finally:
            store.close()
        return 0
    if cmd == "export" and len(argv) == 2:
        store = Store(DB_PATH)
        try:
            print(f"Exported to {interop.export_json(store, argv[1])}")
        finally:
            store.close()
        return 0
    if cmd == "shortcut":
        return _shortcut_cli(argv[1:])
    if cmd == "themes" and argv[1:2] == ["list"]:
        for name, path in theme.available_themes().items():
            print(f"{name}\t{path or '(built-in)'}")
        return 0
    if cmd == "themes" and argv[1:2] == ["install"] and len(argv) == 3:
        names = theme.install_themes(argv[2])
        print(f"Installed {len(names)} theme(s): {', '.join(names)}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


def _shortcut_cli(args) -> int:
    from . import shortcuts

    if not shortcuts.global_available():
        print("Global shortcuts need GNOME (org.gnome.settings-daemon.plugins.media-keys).",
              file=sys.stderr)
        return 1
    if args == ["list"]:
        for action, (title, _, _) in shortcuts.GLOBAL_ACTIONS.items():
            print(f"{action}\t{shortcuts.label(shortcuts.get_global(action))}\t{title}")
        return 0
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    if len(args) != 2 or args[0] not in shortcuts.GLOBAL_ACTIONS:
        print(__doc__, file=sys.stderr)
        return 2
    action, accel = args
    error = shortcuts.validate(accel)
    if error:
        print(error, file=sys.stderr)
        return 1
    conflicts = shortcuts.find_conflicts(accel, ignore_action=action)
    if conflicts and not force:
        print(f"{shortcuts.label(accel)} is already used by:", file=sys.stderr)
        for c in conflicts:
            print(f"  {c.description}", file=sys.stderr)
        print("Pick another key, or add --force to take it over.", file=sys.stderr)
        return 1
    for c in conflicts:
        shortcuts.free(c, accel)
    shortcuts.set_global(action, accel)
    print(f"{action}: {shortcuts.label(accel)}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    migrate_legacy()
    if argv and not argv[0].startswith("-") or argv[:1] in (["--help"], ["-h"], ["--version"]):
        return _cli(argv)

    # GTK 3 cannot place windows on native Wayland. Docking to the screen edge needs
    # XWayland, so prefer the X11 backend unless the user chose a backend explicitly.
    if load_config()["dock"] and os.environ.get("WAYLAND_DISPLAY") and "GDK_BACKEND" not in os.environ:
        os.environ["GDK_BACKEND"] = "x11"

    from .ui import HashiehApp
    return HashiehApp().run([sys.argv[0]] + argv)


if __name__ == "__main__":
    sys.exit(main())
