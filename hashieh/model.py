"""Local note store.

The schema mirrors the SideNotes Core Data model (DataKit/SideNotes.momd):
Folder, Note and Picture entities with the same attribute names, so data can be
round-tripped with the macOS app's JSON/Core Data formats. Dates are stored as
Unix timestamps; conversion to Apple's 2001 reference date happens in interop.
"""

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# colorIdentifier values observed in the macOS binary (NoteColor* / isLastClickedNoteColor*).
# Their order matches the "folder.notes.colors" array of .sntheme files.
NOTE_COLORS = ["Red", "Green", "Blue", "Yellow", "Purple", "Gray"]
TEXT_FORMATTINGS = ["standard", "markdown", "monospaced"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    idx INTEGER NOT NULL DEFAULT 0,
    color_identifier TEXT NOT NULL DEFAULT '',
    is_pinned INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    last_open REAL,
    search_query TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    folder_id TEXT REFERENCES folders(id) ON DELETE CASCADE,
    text TEXT NOT NULL DEFAULT '',
    idx INTEGER NOT NULL DEFAULT 0,
    date REAL NOT NULL,
    text_formatting_id TEXT NOT NULL DEFAULT 'markdown',
    color_identifier TEXT NOT NULL DEFAULT '',
    programming_language TEXT NOT NULL DEFAULT '',
    additional_config TEXT NOT NULL DEFAULT '',
    is_pinned INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    is_locked INTEGER NOT NULL DEFAULT 0,
    is_folded INTEGER NOT NULL DEFAULT 0,
    has_reminder INTEGER NOT NULL DEFAULT 0,
    reminder_date REAL
);
CREATE INDEX IF NOT EXISTS notes_folder ON notes(folder_id);
CREATE TABLE IF NOT EXISTS pictures (
    id TEXT PRIMARY KEY,
    data BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS deleted (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    at REAL NOT NULL
);
"""

# Columns added after the first release, as (table, column, definition, initial value).
# `modified` is when a folder or note last changed on this device; sync compares it.
_MIGRATIONS = [
    ("folders", "modified", "REAL NOT NULL DEFAULT 0", "0"),
    ("notes", "modified", "REAL NOT NULL DEFAULT 0", "date"),
]


def new_id() -> str:
    # SideNotes uses upper-case UUID strings.
    return str(uuid.uuid4()).upper()


@dataclass
class Folder:
    id: str
    name: str = ""
    idx: int = 0
    color_identifier: str = ""
    is_pinned: bool = False
    is_archived: bool = False
    last_open: Optional[float] = None
    search_query: str = ""


@dataclass
class Note:
    id: str
    folder_id: Optional[str]
    text: str = ""
    idx: int = 0
    date: float = field(default_factory=time.time)
    text_formatting_id: str = "markdown"
    color_identifier: str = ""
    programming_language: str = ""
    additional_config: str = ""
    is_pinned: bool = False
    is_archived: bool = False
    is_locked: bool = False
    is_folded: bool = False
    has_reminder: bool = False
    reminder_date: Optional[float] = None

    @property
    def title(self) -> str:
        for line in self.text.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                return line
        return ""


_FOLDER_COLS = ["id", "name", "idx", "color_identifier", "is_pinned", "is_archived",
                "last_open", "search_query"]
# Opening a folder updates last_open; that alone is not a change worth syncing.
_FOLDER_LOCAL = {"last_open"}
_NOTE_COLS = ["id", "folder_id", "text", "idx", "date", "text_formatting_id",
              "color_identifier", "programming_language", "additional_config", "is_pinned",
              "is_archived", "is_locked", "is_folded", "has_reminder", "reminder_date"]
_BOOL_FIELDS = {"is_pinned", "is_archived", "is_locked", "is_folded", "has_reminder"}


def _row_to(cls, cols, row):
    kwargs = {c: (bool(v) if c in _BOOL_FIELDS else v) for c, v in zip(cols, row)}
    return cls(**kwargs)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        for table, col, definition, initial in _MIGRATIONS:
            if col not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                self.db.execute(f"UPDATE {table} SET {col} = {initial}")
        self.db.commit()

    def close(self):
        self.db.close()

    def _upsert(self, table: str, cols: list, vals: list, local=(),
                modified: Optional[float] = None) -> None:
        """Insert or update a row and set its `modified` time.

        Without an explicit `modified`, the time is now, unless only the `local`
        columns changed: then the row keeps its time.
        """
        if modified is None:
            synced = [c for c in cols if c not in local]
            row = self.db.execute(f"SELECT {','.join(synced)}, modified FROM {table} WHERE id=?",
                                  (vals[0],)).fetchone()
            new = [v for c, v in zip(cols, vals) if c not in local]
            modified = row[-1] if row and list(row[:-1]) == new else time.time()
        cols, vals = cols + ["modified"], vals + [modified]
        # Not INSERT OR REPLACE: REPLACE deletes the old row first, which would
        # cascade-delete every note of a folder that is merely being updated.
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
        self.db.execute(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}", vals)
        self.db.execute("DELETE FROM deleted WHERE id=?", (vals[0],))
        self.db.commit()

    def _mark_deleted(self, kind: str, ids: Iterable[str]) -> None:
        now = time.time()
        self.db.executemany("INSERT OR REPLACE INTO deleted (id, kind, at) VALUES (?, ?, ?)",
                            [(i, kind, now) for i in ids])

    # Folders -----------------------------------------------------------------
    def folders(self, include_archived: bool = False) -> list:
        sql = f"SELECT {','.join(_FOLDER_COLS)} FROM folders"
        if not include_archived:
            sql += " WHERE is_archived = 0"
        sql += " ORDER BY is_pinned DESC, idx ASC, name COLLATE NOCASE"
        return [_row_to(Folder, _FOLDER_COLS, r) for r in self.db.execute(sql)]

    def folder(self, folder_id: str) -> Optional[Folder]:
        row = self.db.execute(f"SELECT {','.join(_FOLDER_COLS)} FROM folders WHERE id=?",
                              (folder_id,)).fetchone()
        return _row_to(Folder, _FOLDER_COLS, row) if row else None

    def create_folder(self, name: str) -> Folder:
        top = self.db.execute("SELECT COALESCE(MIN(idx), 0) FROM folders").fetchone()[0]
        folder = Folder(id=new_id(), name=name, idx=top - 1, last_open=time.time())
        self.save_folder(folder)
        return folder

    def save_folder(self, f: Folder, modified: Optional[float] = None) -> None:
        self._upsert("folders", _FOLDER_COLS, [getattr(f, c) for c in _FOLDER_COLS],
                     _FOLDER_LOCAL, modified)

    def delete_folder(self, folder_id: str) -> None:
        notes = [r[0] for r in self.db.execute("SELECT id FROM notes WHERE folder_id=?", (folder_id,))]
        self._mark_deleted("note", notes)
        self._mark_deleted("folder", [folder_id])
        self.db.execute("DELETE FROM folders WHERE id=?", (folder_id,))
        self.db.commit()

    def note_count(self, folder_id: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM notes WHERE folder_id=? AND is_archived=0",
                               (folder_id,)).fetchone()[0]

    # Notes -------------------------------------------------------------------
    def notes(self, folder_id: str) -> list:
        sql = (f"SELECT {','.join(_NOTE_COLS)} FROM notes WHERE folder_id=? AND is_archived=0 "
               "ORDER BY is_pinned DESC, idx ASC, date DESC")
        return [_row_to(Note, _NOTE_COLS, r) for r in self.db.execute(sql, (folder_id,))]

    def all_notes(self) -> list:
        sql = f"SELECT {','.join(_NOTE_COLS)} FROM notes ORDER BY folder_id, idx"
        return [_row_to(Note, _NOTE_COLS, r) for r in self.db.execute(sql)]

    def note(self, note_id: str) -> Optional[Note]:
        row = self.db.execute(f"SELECT {','.join(_NOTE_COLS)} FROM notes WHERE id=?",
                              (note_id,)).fetchone()
        return _row_to(Note, _NOTE_COLS, row) if row else None

    def search(self, query: str) -> list:
        like = "%" + query.replace("%", r"\%").replace("_", r"\_") + "%"
        sql = (f"SELECT {','.join(_NOTE_COLS)} FROM notes WHERE is_archived=0 "
               "AND text LIKE ? ESCAPE '\\' ORDER BY date DESC LIMIT 200")
        return [_row_to(Note, _NOTE_COLS, r) for r in self.db.execute(sql, (like,))]

    def create_note(self, folder_id: str, text: str = "", formatting: str = "markdown") -> Note:
        top = self.db.execute("SELECT COALESCE(MIN(idx), 0) FROM notes WHERE folder_id=?",
                              (folder_id,)).fetchone()[0]
        note = Note(id=new_id(), folder_id=folder_id, text=text, idx=top - 1,
                    text_formatting_id=formatting)
        self.save_note(note)
        return note

    def save_note(self, n: Note, modified: Optional[float] = None) -> None:
        self._upsert("notes", _NOTE_COLS, [getattr(n, c) for c in _NOTE_COLS], (), modified)

    def update_note_text(self, note_id: str, text: str) -> None:
        now = time.time()
        self.db.execute("UPDATE notes SET text=?, date=?, modified=? WHERE id=?",
                        (text, now, now, note_id))
        self.db.commit()

    def delete_note(self, note_id: str) -> None:
        self._mark_deleted("note", [note_id])
        self.db.execute("DELETE FROM notes WHERE id=?", (note_id,))
        self.db.commit()

    def reorder_notes(self, note_ids: Iterable[str]) -> None:
        now = time.time()
        self.db.executemany("UPDATE notes SET idx=?, modified=? WHERE id=? AND idx<>?",
                            [(i, now, nid, i) for i, nid in enumerate(note_ids)])
        self.db.commit()

    # Pictures ----------------------------------------------------------------
    def picture(self, picture_id: str) -> Optional[bytes]:
        row = self.db.execute("SELECT data FROM pictures WHERE id=?", (picture_id,)).fetchone()
        return row[0] if row else None

    def add_picture(self, data: bytes, picture_id: Optional[str] = None) -> str:
        picture_id = picture_id or new_id()
        self.db.execute("INSERT INTO pictures (id, data) VALUES (?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                        (picture_id, sqlite3.Binary(data)))
        self.db.commit()
        return picture_id

    def picture_ids(self) -> list:
        return [r[0] for r in self.db.execute("SELECT id FROM pictures")]

    def is_empty(self) -> bool:
        return self.db.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 0

    # Sync --------------------------------------------------------------------
    def sync_rows(self) -> dict:
        """Folders, notes and deletions as plain dicts keyed by id, for sync.

        Rows carry their `modified` time; deletions are {"kind", "at"}. Columns that
        only matter on this device are left out, and apply_sync leaves them alone.
        """
        rows = {}
        for table, cols in (("folders", _FOLDER_COLS), ("notes", _NOTE_COLS)):
            cols = [c for c in cols if c not in _FOLDER_LOCAL] + ["modified"]
            rows[table] = {r[0]: dict(zip(cols, r))
                           for r in self.db.execute(f"SELECT {','.join(cols)} FROM {table}")}
        rows["deleted"] = {r[0]: {"kind": r[1], "at": r[2]}
                           for r in self.db.execute("SELECT id, kind, at FROM deleted")}
        return rows

    def apply_sync(self, base: dict, merged: dict) -> int:
        """Bring this store from `base` (an earlier sync_rows()) to `merged`.

        A row that changed here since `base` was read is left alone, so an edit made
        while sync ran is kept and goes out with the next sync. Returns the number of
        folders and notes added, changed or removed.
        """
        changed = 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            def unchanged(table, row_id):
                row = self.db.execute(f"SELECT modified FROM {table} WHERE id=?", (row_id,)).fetchone()
                was = base[table].get(row_id)
                return (row[0] if row else None) == (was["modified"] if was else None)

            # Folders before their notes when adding, notes before their folders when removing.
            for table in ("folders", "notes"):
                for row_id, row in merged[table].items():
                    if row != base[table].get(row_id) and unchanged(table, row_id):
                        cols = list(row)
                        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
                        try:
                            self.db.execute(
                                f"INSERT INTO {table} ({','.join(cols)}) "
                                f"VALUES ({','.join('?' * len(cols))}) "
                                f"ON CONFLICT(id) DO UPDATE SET {updates}", list(row.values()))
                        except sqlite3.IntegrityError:  # its folder was just deleted here
                            continue
                        changed += 1
            for table in ("notes", "folders"):
                for row_id in base[table].keys() - merged[table].keys():
                    if unchanged(table, row_id):
                        self.db.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
                        changed += 1
            self.db.execute("DELETE FROM deleted")
            self.db.executemany(
                "INSERT INTO deleted (id, kind, at) SELECT ?, ?, ? WHERE NOT EXISTS "
                "(SELECT 1 FROM folders WHERE id=?1) AND NOT EXISTS (SELECT 1 FROM notes WHERE id=?1)",
                [(i, d["kind"], d["at"]) for i, d in merged["deleted"].items()])
            # Deletions made here while sync ran are kept by the rows' own `unchanged`
            # check above; record them again so they go out next time.
            for kind, table in (("folder", "folders"), ("note", "notes")):
                for row_id in base[table]:
                    if row_id in merged[table] and not self.db.execute(
                            f"SELECT 1 FROM {table} WHERE id=?", (row_id,)).fetchone():
                        self.db.execute("INSERT OR REPLACE INTO deleted (id, kind, at) VALUES (?, ?, ?)",
                                        (row_id, kind, time.time()))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return changed
