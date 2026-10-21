"""Display rendering plan for a note.

SideNotes shows notes rendered: Markdown markup is hidden, `[ ]`/`[x]` become
check circles, `#rrggbb` colors become dots, `[img id]` becomes the picture.
`plan()` turns raw text into style spans plus replacement operations, and
`display_to_raw()` maps an offset in the rendered text back to the raw text so
clicks land in the right place when switching to editing.
"""

from typing import List, NamedTuple, Optional, Tuple

from . import markup

# Spans that become replacement operations instead of styles.
_OP_TAGS = {"markup", "task-box", "task-done-box", "color", "attachment", "separator"}


class Op(NamedTuple):
    start: int          # raw offsets [start, end) being replaced
    end: int
    kind: str           # hide | task | color | image | file | bullet | separator
    data: Optional[str] = None
    size: int = 0       # characters the replacement occupies in the display buffer


def plan(text: str, formatting: str) -> Tuple[List[markup.Span], List[Op]]:
    spans = markup.spans(text, formatting)
    styles = [s for s in spans if s.tag not in _OP_TAGS and s.tag != "list-marker"]
    candidates: List[Op] = []
    for s in spans:
        if s.tag == "markup":
            candidates.append(Op(s.start, s.end, "hide"))
        elif s.tag in ("task-box", "task-done-box"):
            candidates.append(Op(s.start, s.end, "task", "x" if s.tag == "task-done-box" else " ", 1))
        elif s.tag == "color":
            candidates.append(Op(s.start, s.end, "color", s.data, 1))
        elif s.tag == "attachment":
            is_image = text.startswith("[img ", s.start)
            label = "" if is_image else "📁 " + s.data.rstrip("/").rsplit("/", 1)[-1]
            candidates.append(Op(s.start, s.end, "image" if is_image else "file", s.data,
                                 1 if is_image else len(label)))
        elif s.tag == "separator":
            candidates.append(Op(s.start, s.end, "separator", None, 1))
        elif s.tag == "list-marker" and text[s.start:s.end].strip() in ("-", "*"):
            marker = text[s.start:s.end]
            idx = s.start + len(marker) - len(marker.lstrip())
            candidates.append(Op(idx, idx + 1, "bullet", "•", 1))
    # Keep the earliest, then widest, non-overlapping operation.
    candidates.sort(key=lambda o: (o.start, -(o.end - o.start)))
    ops: List[Op] = []
    for op in candidates:
        if op.end <= op.start:
            continue
        if ops and op.start < ops[-1].end:
            continue
        ops.append(op)
    return styles, ops


def display_to_raw(ops: List[Op], offset: int) -> int:
    shift = 0
    for op in ops:
        disp_start = op.start - shift
        if offset < disp_start:
            break
        if offset < disp_start + op.size:
            return op.start
        shift += (op.end - op.start) - op.size
    return offset + shift


def op_at_display(ops: List[Op], offset: int) -> Optional[Op]:
    shift = 0
    for op in ops:
        disp_start = op.start - shift
        if disp_start <= offset < disp_start + max(op.size, 1) and op.size:
            return op
        if offset < disp_start:
            return None
        shift += (op.end - op.start) - op.size
    return None
