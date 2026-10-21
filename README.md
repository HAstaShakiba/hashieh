# Hashieh

*Hashieh* (حاشیه, "margin") is a notes panel for Linux that lives on the edge of your screen. It
has folders, colored note cards, light Markdown, tasks, color swatches, pictures and file links.
It can import and export the notes and themes of
[SideNotes](https://www.apptorium.com/sidenotes) for macOS, so you can move your notes from a Mac.

Website: <https://hastashakiba.github.io/hashieh/>

> **Unofficial.** Hashieh is an independent project. It is not affiliated with or endorsed by
> Apptorium, the makers of SideNotes. It doesn't include any SideNotes code or assets.

![Hashieh in light and dark mode](docs/screenshot.png)

## Install

**Debian / Ubuntu:** download `hashieh_<version>_all.deb` from
[Releases](https://github.com/HAstaShakiba/hashieh/releases), then run:

```bash
sudo apt install ./hashieh_*_all.deb
```

The first time you start Hashieh it turns on *Start at login* and binds **Super+Ctrl+N** to show and
hide the panel. It only binds the key if nothing else on your desktop uses it. You can change or
turn off both in ⚙, and Hashieh won't turn them back on.

**From source** (needs `python3-gi`, `python3-gi-cairo` and `gir1.2-gtk-3.0`):

```bash
git clone https://github.com/HAstaShakiba/hashieh.git
cd hashieh
./install.sh
```

`install.sh` puts `hashieh` in `~/.local/bin` and adds a menu entry. Autostart and the shortcut
are set up on first start, as with the package. You can also choose them up front with
`--autostart` and `--shortcut '<keys>'`. To remove it, run `./install.sh --uninstall`.

## Use

Open the panel with your shortcut, or click the small handle on the screen edge. In the panel:

* Click a note to edit its text. It goes back to the rendered view when you click elsewhere.
* Click a circle to tick a task. Click a link to open it. Click a color dot to copy its value.
* Paste or drop an image to attach it. Drop any other file to link to it.
* Hover over a note to show its toolbar: formatting, sharing, color, moving, copying and deleting.
* Right-click a folder to rename, pin, color or delete it.
* Open **⚙** for theme, light/dark mode, screen side, *Start at login* and *Keyboard shortcuts…*.

Notes support `# headings`, `**bold**`, `*italic*`, `~~strike~~`, `==underline==`,
`::marked::`, `` `code` ``, code blocks, `> quotes`, lists, `[ ]`/`[x]` tasks and `#rrggbb`
colors.

### Keyboard shortcuts

You can change all of these in ⚙ ▸ Keyboard shortcuts…

| Shortcut | Action |
|---|---|
| `Super+Ctrl+N` (global) | show / hide the panel (anywhere on the desktop) |
| `Ctrl+N` | new note (new folder in the folder list) |
| `Ctrl+F` | search |
| `Esc` | stop editing, go back, hide |
| `Ctrl+Delete` | delete the current note |
| `Ctrl+=` / `Ctrl+-` | bigger / smaller text |
| `Ctrl+W` / `Ctrl+Q` | hide / quit |

### Command line

```text
hashieh                              start (or show) the panel
hashieh --toggle | --show | --hide
hashieh --new-note "text"            add a note to the current folder
hashieh --new-note-from-clipboard
hashieh import PATH                  import SideNotes data (see below)
hashieh export DIR                   export data.json + images/
hashieh themes install PATH          install .sntheme files (file, folder or SideNotes.app)
hashieh shortcut toggle '<Super><Control>n'
```

## Moving notes from SideNotes on a Mac

iCloud sync isn't available on Linux, so you move notes as files:

1. On the Mac, quit SideNotes and copy `SideNotes.sqlite` along with its `-wal`/`-shm` files and
   the `.SideNotes_SUPPORT` folder if there is one. Search `~/Library/Containers` and
   `~/Library/Group Containers` for it. A `.snbackup` file from the app's Backups settings also
   works.
2. On Linux, run `hashieh import /path/to/SideNotes.sqlite`, or use ⋯ ▸ Import SideNotes data.

Importing doesn't change the source file, and you can import again later: notes are matched by ID,
and the newer copy wins. To use your SideNotes themes too, run
`hashieh themes install /path/to/SideNotes.app`.

## Wayland

GTK 3 can't place windows on native Wayland. While the panel is docked, Hashieh runs through
XWayland so it can stay on the screen edge and on top of other windows. If you turn docking off
(⚙ ▸ Window ▸ Floating window), it runs as a normal native window.

## Files

* Notes: `~/.local/share/hashieh/notes.db` (SQLite; pictures are stored inside it)
* Themes: `~/.local/share/hashieh/themes/`
* Settings: `~/.config/hashieh/config.json`

## Compatibility notes

The SideNotes data model, the JSON export layout, the text syntax and the `.sntheme` format were
worked out from the shipped macOS app for interoperability. Some details are educated guesses:

* `==text==` is treated as underline.
* *Standard* and *Markdown* note formatting are rendered the same way.
* File links created on a Mac may point to paths that don't exist on Linux.
* The internal layout of `.snbackup` files hasn't been confirmed.

Not implemented yet: reminders, locked notes, sync, and syntax highlighting by language.

## Development

```bash
python3 -m unittest discover -s tests -t .   # tests (GNOME settings use an in-memory backend)
packaging/build.sh                           # dist/: .tar.gz, .deb and SHA256SUMS
```

CI runs the tests and builds the packages on Ubuntu 22.04 and 24.04 for every push and pull request.

To release, bump `__version__` in `hashieh/__init__.py`, optionally add
`packaging/release-notes/vX.Y.Z.md`, commit, and push a matching tag:

```bash
git tag v0.1.1 && git push origin v0.1.1
```

The Release workflow checks that the tag matches the version, runs the tests, builds the packages
and publishes a GitHub release with them. Without a notes file, GitHub generates the notes.

## License

MIT. See [LICENSE](LICENSE).
