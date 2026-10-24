"""Entry point.

    hashieh                              start (or show) the panel
    hashieh --toggle | --show | --hide   show/hide the panel
    hashieh --new-note "text"            add a note to the current folder
    hashieh --new-note-from-clipboard
    hashieh import PATH                  import data.json / SideNotes.sqlite / .snbackup / folder
    hashieh export DIR                   export data.json + images/ to DIR
    hashieh sync [login|logout|status]   sync notes with Google Drive (signs in first if needed)
    hashieh themes install PATH          install .sntheme files (file, folder, or SideNotes.app)
    hashieh themes list
    hashieh shortcut list
    hashieh shortcut toggle|new_note ACCEL [--force]
                                         set a global shortcut, e.g. '<Super><Control>n';
                                         an empty ACCEL removes it, --force takes the key over
    hashieh selftest DIR                 start with temporary data, save a screenshot and a
                                         report to DIR, then quit (used to check builds)
"""

import os
import sys

from . import __version__


def _cli(argv) -> int:
    from . import interop, theme
    from .model import Store
    from .paths import DB_PATH

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
    if cmd == "sync" and len(argv) <= 2:
        return _sync_cli(argv[1:])
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
        print("Global shortcuts aren't available on this desktop.", file=sys.stderr)
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
    if any(c.schema_id == "os" for c in conflicts):
        print(f"{shortcuts.label(accel)} is already used by another program.", file=sys.stderr)
        return 1
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


def _sync_cli(args) -> int:
    import webbrowser

    from . import sync
    from .model import Store
    from .paths import DB_PATH

    if not sync.configured():
        print("Google sync isn't set up in this build of Hashieh.", file=sys.stderr)
        return 1
    account = sync.Account.load()
    action = args[0] if args else "now"
    if action == "status":
        print(f"Signed in as {account.email}" if account else "Not signed in")
        return 0
    if action == "logout":
        if account:
            account.forget()
            account.revoke()
        print("Signed out. Your notes stay on this computer and in Google Drive.")
        return 0
    if action not in ("now", "login"):
        print(__doc__, file=sys.stderr)
        return 2

    def open_url(url):
        print(f"Sign in to Google in your browser. If it doesn't open, visit:\n{url}")
        webbrowser.open(url)

    try:
        if account is None or action == "login":
            account = sync.sign_in(open_url)
            print(f"Signed in as {account.email}")
        store = Store(DB_PATH)
        try:
            print(f"Synced: {sync.sync(store, sync.Drive(account))}")
        finally:
            store.close()
    except sync.SyncError as e:
        if isinstance(e, sync.AuthError):
            sync.Account.forget()
        print(e, file=sys.stderr)
        return 1
    return 0


def _selftest(out_dir: str) -> int:
    import json
    import tempfile
    from pathlib import Path

    # Isolated, throw-away data; must be set before hashieh.paths is imported.
    tmp = tempfile.mkdtemp(prefix="hashieh-selftest-")
    os.environ.update(HASHIEH_DATA_DIR=f"{tmp}/data", HASHIEH_CONFIG_DIR=f"{tmp}/config",
                      XDG_DATA_HOME=f"{tmp}/data", XDG_CONFIG_HOME=f"{tmp}/config")
    sys.modules[__package__].APP_ID = "io.github.hastashakiba.Hashieh.selftest"
    from .ui import HashiehApp
    HashiehApp(selftest_dir=out_dir).run([sys.argv[0]])
    report = Path(out_dir) / "report.json"
    if not report.exists():
        print("selftest: no report written", file=sys.stderr)
        return 1
    data = json.loads(report.read_text())
    print(json.dumps(data, indent=2))
    return 0 if data.get("ok") else 1


def main() -> int:
    if sys.stdout is None:  # windowed builds (Windows .exe, macOS .app) have no console
        sys.stdout = sys.stderr = open(os.devnull, "w")
    argv = sys.argv[1:]
    if argv[:1] == ["selftest"] and len(argv) == 2:
        return _selftest(argv[1])

    from .paths import IS_LINUX, load_config, migrate_legacy
    migrate_legacy()
    if argv and not argv[0].startswith("-") or argv[:1] in (["--help"], ["-h"], ["--version"]):
        return _cli(argv)

    # macOS passes -psn_* when started from Finder on old systems; ignore it.
    argv = [a for a in argv if not a.startswith("-psn_")]
    if not IS_LINUX:
        from . import ipc
        if ipc.forward(argv):  # already running: let that instance handle it
            return 0

    # GTK 3 cannot place windows on native Wayland. Docking to the screen edge needs
    # XWayland, so prefer the X11 backend unless the user chose a backend explicitly.
    if load_config()["dock"] and os.environ.get("WAYLAND_DISPLAY") and "GDK_BACKEND" not in os.environ:
        os.environ["GDK_BACKEND"] = "x11"

    from .ui import HashiehApp
    return HashiehApp().run([sys.argv[0]] + argv)


if __name__ == "__main__":
    sys.exit(main())
