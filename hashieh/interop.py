"""Import/export between this client and the macOS SideNotes data formats.

Supported sources (all recovered from the shipped macOS app):

* JSON, `{"version": 1, "folders": [{"id", "name", "lastOpen", "notes": [...]}]}`.
  This is the layout of the bundled Showcase.json and of the legacy `data.json`
  that the macOS app's old-data importer reads. Dates are seconds since
  2001-01-01 (Apple reference date).
* A legacy data folder: `data.json` plus an `images/` directory.
* The Core Data store `SideNotes.sqlite` (entities Folder/Note/Picture, tables
  ZFOLDER/ZNOTE/ZPICTURE), copied from a Mac.
* A `.snbackup` file or folder, searched for either of the above.
"""

import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from .model import Folder, Note, Store

APPLE_EPOCH = 978307200.0  # 2001-01-01T00:00:00Z as a Unix timestamp

_IMAGE_EXT = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"GIF8": "gif", b"RIFF": "webp",
              b"II*\x00": "tif", b"MM\x00*": "tif"}


def apple_to_unix(value) -> Optional[float]:
    return None if value is None else float(value) + APPLE_EPOCH


def unix_to_apple(value) -> Optional[float]:
    return None if value is None else float(value) - APPLE_EPOCH


def image_extension(data: bytes) -> str:
    for magic, ext in _IMAGE_EXT.items():
        if data.startswith(magic):
            return ext
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "heic"
    return "bin"


class ImportStats:
    def __init__(self):
        self.folders = self.notes = self.pictures = self.skipped = 0

    def __str__(self):
        return (f"{self.folders} folders, {self.notes} notes, {self.pictures} pictures"
                + (f", {self.skipped} kept (local copy newer)" if self.skipped else ""))


def _upsert_note(store: Store, note: Note, stats: ImportStats) -> None:
    existing = store.note(note.id)
    if existing and existing.date > note.date:
        stats.skipped += 1
        return
    store.save_note(note)
    stats.notes += 1


# JSON -------------------------------------------------------------------------

def import_json_data(store: Store, data: dict, images_dir: Optional[Path] = None,
                     stats: Optional[ImportStats] = None) -> ImportStats:
    stats = stats or ImportStats()
    for fi, f in enumerate(data.get("folders", [])):
        folder = store.folder(f["id"]) or Folder(id=f["id"])
        folder.name = f.get("name", folder.name)
        folder.idx = f.get("index", fi)
        folder.color_identifier = f.get("colorIdentifier", folder.color_identifier) or ""
        folder.is_pinned = bool(f.get("isPinned", folder.is_pinned))
        folder.is_archived = bool(f.get("isArchived", folder.is_archived))
        folder.last_open = apple_to_unix(f.get("lastOpen"))
        store.save_folder(folder)
        stats.folders += 1
        for ni, n in enumerate(f.get("notes", [])):
            note = Note(
                id=n["id"], folder_id=folder.id, text=n.get("text", ""),
                idx=n.get("index", ni), date=apple_to_unix(n.get("date", 0)),
                text_formatting_id=n.get("textFormattingID", "markdown"),
                color_identifier=n.get("colorIdentifier", "") or "",
                programming_language=n.get("programmingLanguage", "") or "",
                is_pinned=bool(n.get("isPinned", False)),
                is_archived=bool(n.get("isArchived", False)),
                is_locked=bool(n.get("isLocked", False)),
                is_folded=bool(n.get("isFolded", False)),
            )
            _upsert_note(store, note, stats)
    if images_dir and images_dir.is_dir():
        for p in images_dir.iterdir():
            if p.is_file():
                store.add_picture(p.read_bytes(), p.stem)
                stats.pictures += 1
    return stats


def import_json(store: Store, path: Path) -> ImportStats:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    images = Path(path).parent / "images"
    return import_json_data(store, data, images if images.is_dir() else None)


def export_json(store: Store, out: Path) -> Path:
    """Export to JSON. If `out` is a directory, writes data.json + images/ inside it."""
    out = Path(out)
    images_dir = None
    if out.suffix.lower() != ".json":
        out.mkdir(parents=True, exist_ok=True)
        images_dir = out / "images"
        out = out / "data.json"
    folders = []
    for f in store.folders(include_archived=True):
        notes = [{
            "id": n.id, "text": n.text, "date": unix_to_apple(n.date), "index": n.idx,
            "textFormattingID": n.text_formatting_id,
            **({"colorIdentifier": n.color_identifier} if n.color_identifier else {}),
            **({"isPinned": True} if n.is_pinned else {}),
            **({"programmingLanguage": n.programming_language} if n.programming_language else {}),
        } for n in store.notes(f.id)]
        folders.append({
            "id": f.id, "name": f.name, "index": f.idx, "notes": notes,
            "lastOpen": unix_to_apple(f.last_open) if f.last_open else unix_to_apple(0),
            **({"colorIdentifier": f.color_identifier} if f.color_identifier else {}),
            **({"isPinned": True} if f.is_pinned else {}),
        })
    out.write_text(json.dumps({"version": 1, "folders": folders}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    if images_dir:
        images_dir.mkdir(exist_ok=True)
        for pid in store.picture_ids():
            data = store.picture(pid)
            (images_dir / f"{pid}.{image_extension(data)}").write_bytes(data)
    return out


# Core Data --------------------------------------------------------------------

def _external_blob(blob: bytes, store_dir: Path) -> Optional[bytes]:
    """Resolve a Core Data 'Allows External Storage' blob.

    Core Data prefixes such blobs with 0x01 (inline bytes follow) or 0x02 (an
    ASCII UUID naming a file in .<Store>_SUPPORT/_EXTERNAL_DATA/ follows).
    """
    if not blob:
        return None
    if blob[0] == 1 and image_extension(blob[1:]) != "bin":
        return blob[1:]
    if blob[0] == 2:
        ref = blob[1:].split(b"\x00", 1)[0].decode("ascii", "ignore")
        for support in (".SideNotes_SUPPORT", "SideNotes_SUPPORT"):
            candidate = store_dir / support / "_EXTERNAL_DATA" / ref
            if candidate.is_file():
                return candidate.read_bytes()
        return None
    return blob


def _columns(db, table):
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def import_coredata(store: Store, sqlite_path: Path) -> ImportStats:
    sqlite_path = Path(sqlite_path)
    stats = ImportStats()
    with tempfile.TemporaryDirectory() as tmp:
        # Copy with the WAL so uncheckpointed changes are included, and never touch the original.
        for suffix in ("", "-wal", "-shm"):
            src = sqlite_path.with_name(sqlite_path.name + suffix)
            if src.exists():
                shutil.copy2(src, Path(tmp) / (sqlite_path.name + suffix))
        db = sqlite3.connect(str(Path(tmp) / sqlite_path.name))
        try:
            fcols, ncols = _columns(db, "ZFOLDER"), _columns(db, "ZNOTE")

            EMPTY, MD = "''", "'markdown'"

            def col(cols, name, default="NULL"):
                return name if name in cols else default

            pk_to_folder = {}
            rows = db.execute(
                f"SELECT Z_PK, ZID, {col(fcols, 'ZNAME', EMPTY)}, {col(fcols, 'ZINDEX', 0)}, "
                f"{col(fcols, 'ZCOLORIDENTIFIER', EMPTY)}, {col(fcols, 'ZISPINNED', 0)}, "
                f"{col(fcols, 'ZISARCHIVED', 0)}, {col(fcols, 'ZLASTOPEN')}, "
                f"{col(fcols, 'ZISHIDDENDELETED', 0)} FROM ZFOLDER")
            for pk, fid, name, idx, color, pinned, archived, last_open, hidden in rows:
                if hidden or not fid:
                    continue
                store.save_folder(Folder(id=fid, name=name or "", idx=idx or 0,
                                         color_identifier=color or "", is_pinned=bool(pinned),
                                         is_archived=bool(archived),
                                         last_open=apple_to_unix(last_open)))
                pk_to_folder[pk] = fid
                stats.folders += 1

            rows = db.execute(
                f"SELECT ZID, ZFOLDER, {col(ncols, 'ZTEXT', EMPTY)}, {col(ncols, 'ZINDEX', 0)}, "
                f"{col(ncols, 'ZDATE', 0)}, {col(ncols, 'ZTEXTFORMATTINGID', MD)}, "
                f"{col(ncols, 'ZCOLORIDENTIFIER', EMPTY)}, {col(ncols, 'ZISPINNED', 0)}, "
                f"{col(ncols, 'ZISARCHIVED', 0)}, {col(ncols, 'ZISLOCKED', 0)}, "
                f"{col(ncols, 'ZISFOLDED', 0)}, {col(ncols, 'ZPROGRAMMINGLANGUAGE', EMPTY)}, "
                f"{col(ncols, 'ZISHIDDENDELETED', 0)} FROM ZNOTE")
            for (nid, fpk, text, idx, date, fmt, color, pinned, archived, locked, folded,
                 lang, hidden) in rows:
                if hidden or not nid or fpk not in pk_to_folder:
                    continue
                _upsert_note(store, Note(
                    id=nid, folder_id=pk_to_folder[fpk], text=text or "", idx=idx or 0,
                    date=apple_to_unix(date or 0), text_formatting_id=fmt or "markdown",
                    color_identifier=color or "", is_pinned=bool(pinned),
                    is_archived=bool(archived), is_locked=bool(locked), is_folded=bool(folded),
                    programming_language=lang or ""), stats)

            if "ZID" in _columns(db, "ZPICTURE"):
                pcols = _columns(db, "ZPICTURE")
                hidden_col = col(pcols, "ZISHIDDENDELETED", 0)
                for pid, blob, hidden in db.execute(
                        f"SELECT ZID, ZDATA, {hidden_col} FROM ZPICTURE"):
                    data = _external_blob(bytes(blob or b""), sqlite_path.parent)
                    if pid and data and not hidden:
                        store.add_picture(data, pid)
                        stats.pictures += 1
        finally:
            db.close()
    return stats


# Dispatch ---------------------------------------------------------------------

def _import_dir(store: Store, root: Path) -> ImportStats:
    sqlite = next(iter(sorted(root.rglob("*.sqlite"))), None)
    if sqlite:
        return import_coredata(store, sqlite)
    data = next(iter(sorted(root.rglob("data.json"))), None) or \
        next(iter(sorted(root.rglob("*.json"))), None)
    if data:
        return import_json(store, data)
    raise ValueError(f"No SideNotes data (SideNotes.sqlite or data.json) found in {root}")


def import_any(store: Store, path: Path) -> ImportStats:
    path = Path(path).expanduser()
    if path.is_dir():
        return _import_dir(store, path)
    if path.suffix == ".sqlite":
        return import_coredata(store, path)
    if path.suffix == ".json":
        return import_json(store, path)
    if zipfile.is_zipfile(path):
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path) as zf:
            root = Path(tmp).resolve()
            for member in zf.namelist():
                if not (root / member).resolve().is_relative_to(root):
                    raise ValueError(f"Refusing unsafe path in archive: {member}")
            zf.extractall(root)
            return _import_dir(store, root)
    raise ValueError(f"Unrecognised SideNotes data: {path}")
