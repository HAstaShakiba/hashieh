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
"""


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
        self.db.commit()

    def close(self):
        self.db.close()

    def _upsert(self, table: str, cols: list, vals: list) -> None:
        # Not INSERT OR REPLACE: REPLACE deletes the old row first, which would
        # cascade-delete every note of a folder that is merely being updated.
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
        self.db.execute(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}", vals)
        self.db.commit()

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

    def save_folder(self, f: Folder) -> None:
        self._upsert("folders", _FOLDER_COLS, [getattr(f, c) for c in _FOLDER_COLS])

    def delete_folder(self, folder_id: str) -> None:
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

    def save_note(self, n: Note) -> None:
        self._upsert("notes", _NOTE_COLS, [getattr(n, c) for c in _NOTE_COLS])

    def update_note_text(self, note_id: str, text: str) -> None:
        self.db.execute("UPDATE notes SET text=?, date=? WHERE id=?", (text, time.time(), note_id))
        self.db.commit()

    def delete_note(self, note_id: str) -> None:
        self.db.execute("DELETE FROM notes WHERE id=?", (note_id,))
        self.db.commit()

    def reorder_notes(self, note_ids: Iterable[str]) -> None:
        self.db.executemany("UPDATE notes SET idx=? WHERE id=?",
                            [(i, nid) for i, nid in enumerate(note_ids)])
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
