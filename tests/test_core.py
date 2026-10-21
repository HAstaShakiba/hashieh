import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hashieh import interop, markup, render, theme
from hashieh.model import Store

SHOWCASE = {
    "version": 1,
    "folders": [{
        "id": "F1", "name": "Showcase", "lastOpen": 583836420.7,
        "notes": [
            {"id": "N1", "date": 579866043, "textFormattingID": "standard",
             "text": "# Tasks\n[x] Done\n[ ] Todo #87BC4E"},
            {"id": "N2", "date": 579866067, "textFormattingID": "markdown", "colorIdentifier": "Blue",
             "text": "*italic* **bold** ***both*** ~~strike~~ `code` ::mark:: ==under==\n[img P1]"},
        ],
    }],
}


def tags(text, fmt="markdown"):
    return {(text[s.start:s.end], s.tag) for s in markup.spans(text, fmt)}


class MarkupTests(unittest.TestCase):
    def test_inline(self):
        t = tags("*italic* **bold** ***both*** ~~strike~~ `code` ::mark:: ==under==")
        for expected in [("italic", "italic"), ("bold", "bold"), ("both", "bolditalic"),
                         ("strike", "strike"), ("code", "code"), ("mark", "mark"),
                         ("under", "underline")]:
            self.assertIn(expected, t)
        self.assertNotIn(("bold", "italic"), t)

    def test_blocks(self):
        t = tags("# Title\n> quoted\n- item\n1. first\n---\n```\nx = 1\n```")
        self.assertIn(("# Title", "h1"), t)
        self.assertIn(("> quoted", "quote"), t)
        self.assertIn(("-", "list-marker"), t)
        self.assertIn(("1. ", "list-marker"), t)
        self.assertIn(("---", "separator"), t)
        self.assertTrue(any(tag == "codeblock" for _, tag in t))

    def test_tasks_colors_links_attachments(self):
        text = "[ ] open\n[x] closed #3399ff https://example.com [img ABC] [file /tmp/a.txt]"
        t = tags(text)
        self.assertIn(("[ ]", "task-box"), t)
        self.assertIn(("[x]", "task-done-box"), t)
        self.assertIn(("#3399ff", "color"), t)
        self.assertIn(("https://example.com", "link"), t)
        self.assertIn(("[img ABC]", "attachment"), t)
        self.assertEqual(markup.image_ids(text), ["ABC"])
        self.assertEqual(markup.file_refs(text), ["/tmp/a.txt"])

    def test_monospaced_skips_markdown(self):
        t = tags("**not bold** https://x.org", "monospaced")
        self.assertNotIn(("not bold", "bold"), t)
        self.assertIn(("https://x.org", "link"), t)

    def test_toggle_task(self):
        text = "a\n[ ] one\n[x] two"
        self.assertEqual(markup.toggle_task(text, text.index("[ ]") + 1), "a\n[x] one\n[x] two")
        self.assertEqual(markup.toggle_task(text, text.index("[x]")), "a\n[ ] one\n[ ] two")
        self.assertIsNone(markup.toggle_task(text, 0))


class RenderTests(unittest.TestCase):
    def apply(self, text, fmt="markdown"):
        styles, ops = render.plan(text, fmt)
        out, pos = [], 0
        for op in ops:
            out.append(text[pos:op.start])
            out.append({"hide": "", "task": "O", "color": "@", "image": "I", "separator": "-",
                        "bullet": "•"}.get(op.kind, "F" * op.size))
            pos = op.end
        out.append(text[pos:])
        return "".join(out), ops

    def test_markup_hidden_and_replacements(self):
        shown, _ = self.apply("# Hello\n**bold** and #ff0000\n[x] done\n- item\n[img P1]")
        self.assertEqual(shown, "Hello\nbold and @\nO done\n• item\nI")

    def test_display_to_raw(self):
        text = "# Hi **b** x"
        shown, ops = self.apply(text)
        self.assertEqual(shown, "Hi b x")
        for disp, raw in [(0, 2), (3, 7), (5, 11)]:
            self.assertEqual(shown[disp], text[raw])
            self.assertEqual(render.display_to_raw(ops, disp), raw)

    def test_op_at_display(self):
        text = "x\n[ ] b #00ff00"
        shown, ops = self.apply(text)
        self.assertEqual(render.op_at_display(ops, shown.index("O")).kind, "task")
        self.assertEqual(render.op_at_display(ops, shown.index("@")).data, "#00ff00")
        self.assertIsNone(render.op_at_display(ops, 0))


class InteropTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = Store(self.dir / "db.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_json_roundtrip(self):
        src = self.dir / "in" / "data.json"
        (src.parent / "images").mkdir(parents=True)
        src.write_text(json.dumps(SHOWCASE))
        (src.parent / "images" / "P1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        stats = interop.import_any(self.store, src)
        self.assertEqual((stats.folders, stats.notes, stats.pictures), (1, 2, 1))
        note = self.store.note("N2")
        self.assertEqual(note.color_identifier, "Blue")
        self.assertAlmostEqual(note.date, 579866067 + interop.APPLE_EPOCH)

        out = interop.export_json(self.store, self.dir / "out")
        data = json.loads(out.read_text())
        self.assertEqual(data["version"], 1)
        exported = {n["id"]: n for n in data["folders"][0]["notes"]}
        self.assertEqual(exported["N2"]["date"], 579866067)
        self.assertTrue((self.dir / "out" / "images" / "P1.png").exists())

    def test_saving_folder_keeps_its_notes(self):
        folder = self.store.create_folder("Keep")
        self.store.create_note(folder.id, "survives")
        folder.name = "Renamed"
        self.store.save_folder(folder)
        self.assertEqual([n.text for n in self.store.notes(folder.id)], ["survives"])

    def test_coredata_import(self):
        path = self.dir / "SideNotes.sqlite"
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE ZFOLDER (Z_PK INTEGER PRIMARY KEY, ZID TEXT, ZNAME TEXT, ZINDEX INTEGER,
                ZISHIDDENDELETED INTEGER, ZLASTOPEN REAL);
            CREATE TABLE ZNOTE (Z_PK INTEGER PRIMARY KEY, ZID TEXT, ZFOLDER INTEGER, ZTEXT TEXT,
                ZDATE REAL, ZTEXTFORMATTINGID TEXT, ZCOLORIDENTIFIER TEXT, ZISHIDDENDELETED INTEGER);
            CREATE TABLE ZPICTURE (Z_PK INTEGER PRIMARY KEY, ZID TEXT, ZDATA BLOB);
            INSERT INTO ZFOLDER VALUES (1, 'F1', 'Work', 0, 0, 700000000), (2, 'F2', 'Gone', 1, 1, NULL);
            INSERT INTO ZNOTE VALUES (1, 'N1', 1, 'hello', 700000000, 'markdown', 'Red', 0),
                                     (2, 'N2', 1, 'deleted', 700000000, 'markdown', '', 1);
        """)
        db.execute("INSERT INTO ZPICTURE VALUES (1, 'P1', ?)", (b"\x01\x89PNG\r\n\x1a\nxx",))
        db.commit()
        db.close()
        stats = interop.import_coredata(self.store, path)
        self.assertEqual((stats.folders, stats.notes, stats.pictures), (1, 1, 1))
        self.assertEqual(self.store.note("N1").color_identifier, "Red")
        self.assertTrue(self.store.picture("P1").startswith(b"\x89PNG"))


class ThemeTests(unittest.TestCase):
    def test_builtin_and_css(self):
        for dark in (False, True):
            t = theme.load("Default", dark)
            css = theme.build_css(t, 11)
            self.assertIn(".sn-note-blue", css)
            self.assertEqual(theme.css_color("#FF000080"), "rgba(255,0,0,0.502)")

    def _load_custom(self, data, dark):
        with tempfile.TemporaryDirectory() as tmp:
            old = theme.THEMES_DIR
            theme.THEMES_DIR = Path(tmp)
            try:
                (Path(tmp) / "T.sntheme").write_text(json.dumps(data))
                return theme.load("T", dark)
            finally:
                theme.THEMES_DIR = old

    def test_real_world_theme_quirks(self):
        dark_only = {"dark": {"folder": {
            "header": {"backgroundColor": "#06315c99", "textColor": "#fff"},
            "notes": {"clean": {"backgroundColor": "#261d45", "textColor": "#00ff9c"},
                      "colors": [{"backgroundColor": "#FFA6A1"}]}}}}
        t = self._load_custom(dark_only, dark=False)  # only a dark variant exists
        clean = theme.note_style(t, "")
        self.assertEqual(t["systemAppearance"], "dark")
        self.assertEqual(clean["backgroundColor"], "#261D45FF")      # 6-digit expanded
        self.assertNotIn("backgroundGradient", clean)                # built-in gradient not leaked
        self.assertNotIn("backgroundGradient", theme.note_style(t, "Red"))
        self.assertEqual(t["folder"]["header"]["textColor"], "#FFFFFFFF")  # #fff expanded
        self.assertTrue(t["folder"]["header"]["backgroundColor"].endswith("FF"))  # flattened
        self.assertIn("rgba(255,255,255,1.000)", theme.build_css(t, 11))

    def test_bundled_themes_have_readable_notes(self):
        bundle = Path(__file__).resolve().parents[2] / "SideNotes.app/Contents/Resources"
        files = sorted(bundle.glob("*.sntheme"))
        if not files:
            self.skipTest("SideNotes.app not available")
        for f in files:
            for dark in (False, True):
                t = self._load_custom(json.loads(f.read_text()), dark)
                for ident in ["", "Red", "Blue"]:
                    st = theme.note_style(t, ident)
                    bg = st.get("backgroundGradient", {}).get("start") or st["backgroundColor"]
                    text = theme.blend_hex(st["textColor"], bg)
                    contrast = abs(theme._luminance(text + "FF") - theme._luminance(bg))
                    self.assertGreater(contrast, 0.3, f"{f.stem} dark={dark} {ident or 'clean'}")

    def test_note_style_merges_color(self):
        t = theme.load("Default", False)
        self.assertNotEqual(theme.note_style(t, "Red")["backgroundColor"],
                            theme.note_style(t, "")["backgroundColor"])


if __name__ == "__main__":
    unittest.main()
