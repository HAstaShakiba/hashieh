"""SideNotes text formatting.

The regular expressions are ported from the patterns embedded in the macOS
binary (AptTextView2 / SideNotes). ICU-only constructs were rewritten for
Python's `re` (variable-width look-behind such as `(?<=^|\\n)` became `(?m)^`).

`spans(text, formatting)` returns (start, end, tag) tuples in character
offsets, which map directly onto Gtk.TextBuffer offsets.
"""

import re
from typing import List, NamedTuple, Optional


class Span(NamedTuple):
    start: int
    end: int
    tag: str
    data: Optional[str] = None


# Inline styles: (regex, content tag). Group 1 and 3 are markup, group 2 the content.
_INLINE = [
    (re.compile(r"(?<!\\)(?<!\*)(\*\*\*)((?!\*)(?!\s).+?)(?<!\s)(?<!\\)(\*\*\*)(?!\*)"), "bolditalic"),
    (re.compile(r"(?<!\\)(?<!\*)(\*\*)((?!\*)(?!\s).+?)(?<![\s*])(?<!\\)(\*\*)(?!\*)"), "bold"),
    (re.compile(r"(?<!\\)(?<!\*)(\*{1})((?!\*)(?!\s).+?)(?<!\s)(?<!\\)(\*)(?!\*)"), "italic"),
    (re.compile(r"(?<!\\)(?<!~)(~~)((?!~)(?!\s).+?)(?<!\s)(?<!\\)(~~)(?!~)"), "strike"),
    (re.compile(r"(?<!\\)(?<!:)(::)((?!:)(?!\s).+?)(?<!\s)(?<!\\)(::)(?!:)"), "mark"),
    (re.compile(r"(?<!\\)(?<!=)(==)((?!=)(?!\s).+?)(?<!\s)(?<!\\)(==)(?!=)"), "underline"),
    (re.compile(r"(?<!\\)(?<!`)(`)((?!`).+?)(?<!\\)(`)(?!`)"), "code"),
]

CODE_BLOCK = re.compile(r"(?m)^(?<!\\)(```(\w*)\n)(\s*[^`]+?)(\s*(?<!\\)\n```)(?=\n|$)")
HEADER = re.compile(r"(?m)^(#{1,6})([ \t]+)(.*)$")
QUOTE = re.compile(r"(?m)^(>[ \t]?)(.*)$")
TASK = re.compile(r"(?m)^([\t ]*\[([ |xX])\][\t ]*)(.*)$")
BULLET = re.compile(r"(?m)^(([ \t]*[*-])[ ])(.*)$")
NUMBERED = re.compile(r"(?m)^(([ \t]*)(\d+)\.[ ])(.*)$")
SEPARATOR = re.compile(r"(?m)^(---+|\*\*\*+)[ \t]*$")
MD_LINK = re.compile(r"(\[)([^\]\n]*)(\])(\()(.*?)(\))")
URL = re.compile(r"(?<!\]\()(?!\[\w+\s+)\b[a-zA-Z][a-zA-Z\d+\-.]*://[^\s\])]+")
COLOR = re.compile(r"(?<![\w#])#([0-9a-fA-F]{6})\b")
IMAGE = re.compile(r"\[img (.*?)\]")
FILE = re.compile(r"\[file (.*?)\]")


def image_ids(text: str) -> List[str]:
    return [m.group(1).strip() for m in IMAGE.finditer(text)]


def file_refs(text: str) -> List[str]:
    return [m.group(1).strip() for m in FILE.finditer(text)]


def _overlaps(start: int, end: int, ranges) -> bool:
    return any(start < e and s < end for s, e in ranges)


def spans(text: str, formatting: str = "markdown") -> List[Span]:
    out: List[Span] = []
    protected = []  # ranges where inline markdown must not apply (code, urls, attachments)

    # Attachments and links are recognised in every formatting mode.
    for m in IMAGE.finditer(text):
        out.append(Span(m.start(), m.end(), "attachment", m.group(1).strip()))
        protected.append(m.span())
    for m in FILE.finditer(text):
        out.append(Span(m.start(), m.end(), "attachment", m.group(1).strip()))
        protected.append(m.span())
    for m in TASK.finditer(text):
        box_start = m.start(1) + m.group(1).index("[")
        done = m.group(2).lower() == "x"
        out.append(Span(box_start, box_start + 3, "task-done-box" if done else "task-box"))
        if done and m.group(3):
            out.append(Span(m.start(3), m.end(3), "task-done"))
    for m in COLOR.finditer(text):
        out.append(Span(m.start(), m.end(), "color", "#" + m.group(1)))

    if formatting == "monospaced":
        for m in URL.finditer(text):
            out.append(Span(m.start(), m.end(), "link", m.group(0)))
        return out

    for m in CODE_BLOCK.finditer(text):
        out.append(Span(m.start(), m.end(), "codeblock"))
        out.append(Span(m.start(1), m.end(1), "markup"))
        out.append(Span(m.start(4), m.end(4), "markup"))
        protected.append(m.span())

    for m in MD_LINK.finditer(text):
        if _overlaps(m.start(), m.end(), protected):
            continue
        out.append(Span(m.start(1), m.end(1), "markup"))
        out.append(Span(m.start(2), m.end(2), "link", m.group(5)))
        out.append(Span(m.start(3), m.end(6), "markup"))
        protected.append(m.span())
    for m in URL.finditer(text):
        if _overlaps(m.start(), m.end(), protected):
            continue
        out.append(Span(m.start(), m.end(), "link", m.group(0)))
        protected.append(m.span())

    for m in HEADER.finditer(text):
        level = len(m.group(1))
        out.append(Span(m.start(), m.end(), "h1" if level == 1 else "h2" if level == 2 else "h3"))
        out.append(Span(m.start(1), m.end(2), "markup"))
    for m in QUOTE.finditer(text):
        out.append(Span(m.start(), m.end(), "quote"))
        out.append(Span(m.start(1), m.end(1), "markup"))
    for m in BULLET.finditer(text):
        if TASK.match(text, m.start()):
            continue
        out.append(Span(m.start(2), m.end(2), "list-marker"))
    for m in NUMBERED.finditer(text):
        out.append(Span(m.start(1), m.end(1), "list-marker"))
    for m in SEPARATOR.finditer(text):
        out.append(Span(m.start(), m.end(), "separator"))

    taken = list(protected)
    for regex, tag in _INLINE:
        for m in regex.finditer(text):
            if _overlaps(m.start(), m.end(), taken):
                continue
            out.append(Span(m.start(1), m.end(1), "markup"))
            out.append(Span(m.start(2), m.end(2), tag))
            out.append(Span(m.start(3), m.end(3), "markup"))
            if tag in ("code", "bolditalic", "bold"):
                # stop `**x**` from also matching as nested `*x*` italics
                taken.append(m.span())
    return out


def toggle_task(text: str, offset: int) -> Optional[str]:
    """Toggle the checkbox whose `[ ]` starts at or contains `offset`."""
    for m in TASK.finditer(text):
        box = m.start(1) + m.group(1).index("[")
        if box <= offset <= box + 3:
            mark = " " if m.group(2).lower() == "x" else "x"
            return text[:box + 1] + mark + text[box + 2:]
    return None


def to_plain(text: str) -> str:
    """Strip markup for previews and titles."""
    text = IMAGE.sub("🖼", text)
    text = FILE.sub(lambda m: "📁 " + m.group(1).rsplit("/", 1)[-1], text)
    text = HEADER.sub(r"\3", text)
    for regex, _ in _INLINE:
        text = regex.sub(r"\2", text)
    return text
