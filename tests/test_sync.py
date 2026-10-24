import itertools
import tempfile
import time
import unittest
from pathlib import Path

from hashieh import sync
from hashieh.model import Store


class FakeDrive:
    """In-memory stand-in for sync.Drive."""

    def __init__(self):
        self.data = {}   # id -> (name, bytes, revision)
        self._ids = itertools.count(1)

    def files(self):
        return [{"id": i, "name": n, "headRevisionId": str(r)} for i, (n, _, r) in self.data.items()]

    def revision(self, file_id):
        return str(self.data[file_id][2])

    def download(self, file_id):
        return self.data[file_id][1]

    def create(self, name, data):
        self.data[str(next(self._ids))] = (name, data, 1)

    def update(self, file_id, data):
        name, _, rev = self.data[file_id]
        self.data[file_id] = (name, data, rev + 1)

    def delete(self, file_id):
        del self.data[file_id]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = Store(Path(self.tmp.name) / "a.db")
        self.b = Store(Path(self.tmp.name) / "b.db")
        self.drive = FakeDrive()

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.tmp.cleanup()

    def sync_both(self):
        sync.sync(self.a, self.drive)
        sync.sync(self.b, self.drive)
        sync.sync(self.a, self.drive)

    def texts(self, store):
        return sorted(n.text for n in store.all_notes())

    def test_new_notes_and_pictures_reach_the_other_device(self):
        folder = self.a.create_folder("Work")
        self.a.create_note(folder.id, "from a")
        pid = self.a.add_picture(b"\x89PNG data")
        other = self.b.create_folder("Home")
        self.b.create_note(other.id, "from b")
        self.sync_both()
        for store in (self.a, self.b):
            self.assertEqual(self.texts(store), ["from a", "from b"])
            self.assertEqual({f.name for f in store.folders()}, {"Work", "Home"})
        self.assertEqual(self.b.picture(pid), b"\x89PNG data")
        self.assertEqual(sum(n == sync.SYNC_FILE for n, _, _ in self.drive.data.values()), 1)

    def test_latest_edit_wins(self):
        folder = self.a.create_folder("F")
        note = self.a.create_note(folder.id, "first")
        self.sync_both()
        self.a.update_note_text(note.id, "edited on a")
        time.sleep(0.01)
        self.b.update_note_text(note.id, "edited on b")
        self.sync_both()
        self.assertEqual(self.a.note(note.id).text, "edited on b")
        self.assertEqual(self.b.note(note.id).text, "edited on b")

    def test_deletions_spread(self):
        folder = self.a.create_folder("F")
        keep = self.a.create_note(folder.id, "keep")
        gone = self.a.create_note(folder.id, "gone")
        doomed = self.a.create_folder("Doomed")
        self.a.create_note(doomed.id, "in doomed")
        self.sync_both()
        self.b.delete_note(gone.id)
        self.b.delete_folder(doomed.id)
        self.sync_both()
        for store in (self.a, self.b):
            self.assertEqual(self.texts(store), ["keep"])
            self.assertIsNone(store.folder(doomed.id))
        self.assertIsNotNone(self.a.note(keep.id))

    def test_edit_after_delete_keeps_the_note(self):
        folder = self.a.create_folder("F")
        note = self.a.create_note(folder.id, "x")
        self.sync_both()
        self.b.delete_note(note.id)
        time.sleep(0.01)
        self.a.update_note_text(note.id, "still wanted")
        sync.sync(self.b, self.drive)
        self.sync_both()
        self.assertEqual(self.b.note(note.id).text, "still wanted")

    def test_opening_a_folder_is_not_a_change(self):
        folder = self.a.create_folder("F")
        self.sync_both()
        before = self.a.sync_rows()["folders"][folder.id]["modified"]
        folder.last_open = time.time() + 100
        self.a.save_folder(folder)
        self.assertEqual(self.a.sync_rows()["folders"][folder.id]["modified"], before)
        self.assertFalse(sync.sync(self.a, self.drive).sent)

    def test_edit_during_sync_is_kept(self):
        folder = self.a.create_folder("F")
        note = self.a.create_note(folder.id, "old")
        self.sync_both()
        self.b.update_note_text(note.id, "remote")
        sync.sync(self.b, self.drive)
        base = self.a.sync_rows()
        self.a.update_note_text(note.id, "typed meanwhile")  # after base was read
        merged = sync.merge(base, sync._decode(self.drive.download("1")))
        self.a.apply_sync(base, merged)
        self.assertEqual(self.a.note(note.id).text, "typed meanwhile")

    def test_concurrent_first_sync_copies_are_merged(self):
        self.drive.create(sync.SYNC_FILE, sync._encode(sync.EMPTY))
        folder = self.b.create_folder("B")
        self.b.create_note(folder.id, "b")
        sync.sync(self.b, self.drive)
        folder = self.a.create_folder("A")
        self.a.create_note(folder.id, "a")
        self.drive.create(sync.SYNC_FILE, sync._encode(self.a.sync_rows()))
        sync.sync(self.a, self.drive)
        self.assertEqual(sum(n == sync.SYNC_FILE for n, _, _ in self.drive.data.values()), 1)
        sync.sync(self.b, self.drive)
        self.assertEqual(self.texts(self.b), ["a", "b"])

    def test_merge_prunes_old_deletions(self):
        now = time.time()
        rows = {"folders": {}, "notes": {}, "deleted": {
            "OLD": {"kind": "note", "at": now - sync.KEEP_DELETIONS - 1},
            "NEW": {"kind": "note", "at": now}}}
        self.assertEqual(set(sync.merge(rows, sync.EMPTY, now)["deleted"]), {"NEW"})

    def test_newer_format_is_refused(self):
        with self.assertRaises(sync.SyncError):
            sync._decode(b'{"version": 99}')

    def test_old_database_is_migrated(self):
        import sqlite3
        path = Path(self.tmp.name) / "old.db"
        db = sqlite3.connect(str(path))
        db.executescript("CREATE TABLE folders (id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', "
                         "idx INTEGER NOT NULL DEFAULT 0, color_identifier TEXT NOT NULL DEFAULT '', "
                         "is_pinned INTEGER NOT NULL DEFAULT 0, is_archived INTEGER NOT NULL DEFAULT 0, "
                         "last_open REAL, search_query TEXT NOT NULL DEFAULT '');"
                         "INSERT INTO folders (id, name) VALUES ('F', 'Old');")
        db.close()
        store = Store(path)
        try:
            self.assertEqual(store.sync_rows()["folders"]["F"]["modified"], 0)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
