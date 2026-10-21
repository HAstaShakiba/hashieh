"""Loader for SideNotes `.sntheme` files (JSON with "light"/"dark"/"any" variants).

A theme variant describes the folder list, the folder view, note styles
("clean" plus one entry per note color, in NOTE_COLORS order) and search.
Colors are "#RRGGBBAA" strings.
"""

import json
import shutil
from pathlib import Path
from typing import Dict, Optional

from .model import NOTE_COLORS
from .paths import THEMES_DIR


def _note(bg, bg2, text, accent, bar, code_bg):
    return {
        "backgroundColor": bg, "backgroundGradient": {"start": bg, "end": bg2},
        "textColor": text, "secondaryTextColor": text[:7] + "AA", "tertiaryTextColor": text[:7] + "77",
        "header": {"textColor": accent, "isBold": True}, "buttonColor": accent, "barColor": bar,
        "linkColor": "#2F6FD6FF", "quote": {"textColor": text[:7] + "AA"},
        "code": {"backgroundColor": code_bg, "textColor": accent},
        "codeBlock": {"backgroundColor": code_bg, "textColor": accent},
        "markdownMarkup": {"textColor": text[:7] + "66"},
        "mark": {"backgroundColor": "#F2E86DFF", "textColor": "#2B2A10FF"},
        "filePreview": {"backgroundColor": code_bg, "textColor": text},
        "checkMark": {"borderColor": text[:7] + "88", "doneBorderColor": accent,
                      "checkMarkColor": accent},
        "separator": {"color": accent[:7] + "40", "height": 1},
    }


def _variant(dark: bool) -> dict:
    if dark:
        clean = _note("#2B2E33FF", "#272A2FFF", "#E3E5E8FF", "#8AB4F8FF", "#3D4450FF", "#353A42FF")
        colors = [_note(c, c, "#E8E6E3FF", a, a[:7] + "55", "#00000033") for c, a in [
            ("#3E2C31FF", "#F28B9BFF"), ("#2D3A2CFF", "#93C98AFF"), ("#2B3445FF", "#8AB4F8FF"),
            ("#3D3727FF", "#E6C46AFF"), ("#352E45FF", "#B79CF2FF"), ("#33363BFF", "#A9B1BDFF")]]
        base = {"bg": "#1E2024FF", "header": "#24272CFF", "text": "#E3E5E8FF", "accent": "#8AB4F8FF"}
    else:
        clean = _note("#FFFFFFFF", "#FAFBFDFF", "#2E3238FF", "#3A5BA0FF", "#D9E2F2FF", "#EDF1F8FF")
        colors = [_note(c, c, "#33302EFF", a, a[:7] + "44", "#00000010") for c, a in [
            ("#FCEEF1FF", "#B5536DFF"), ("#EEF6ECFF", "#4F8A47FF"), ("#ECF2FBFF", "#3F6EB5FF"),
            ("#FBF5E4FF", "#A07B2EFF"), ("#F3EEFBFF", "#7558B0FF"), ("#F1F3F6FF", "#62708AFF")]]
        base = {"bg": "#ECEFF4FF", "header": "#F6F7FAFF", "text": "#30343AFF", "accent": "#3A5BA0FF"}
    return {
        "systemAppearance": "dark" if dark else "light",
        "accentColor": base["accent"],
        "folders": {
            "header": {"backgroundColor": base["header"], "textColor": base["accent"],
                       "buttonColor": base["accent"]},
            "list": {"backgroundColor": base["bg"], "textColor": base["text"],
                     "secondaryTextColor": base["text"][:7] + "99", "buttonColor": base["accent"]},
            "cleanFolderColor": base["accent"],
        },
        "folder": {
            "backgroundColor": base["bg"],
            "header": {"backgroundColor": base["header"], "textColor": base["accent"],
                       "buttonColor": base["accent"]},
            "notes": {"clean": clean, "colors": colors},
        },
        "search": {"backgroundColor": base["bg"],
                   "searchField": {"backgroundColor": clean["backgroundColor"],
                                   "textColor": base["text"]}},
    }


BUILTIN = {"light": _variant(False), "dark": _variant(True)}


def available_themes() -> Dict[str, Optional[Path]]:
    themes = {"Default": None}
    if THEMES_DIR.is_dir():
        for p in sorted(THEMES_DIR.glob("*.sntheme")):
            themes[p.stem] = p
    return themes


def install_themes(source: Path) -> list:
    """Copy .sntheme files from a file, a folder, or a SideNotes.app bundle the user owns."""
    source = Path(source).expanduser()
    files = [source] if source.is_file() else sorted(source.rglob("*.sntheme"))
    THEMES_DIR.mkdir(parents=True, exist_ok=True)
    installed = []
    for f in files:
        json.loads(f.read_text())  # validate before copying
        name = "Default (SideNotes)" if f.stem == "Default" else f.stem
        shutil.copy2(f, THEMES_DIR / f"{name}.sntheme")
        installed.append(name)
    return installed


def load(name: str, dark: bool) -> dict:
    """Return the variant of theme `name` for the requested appearance, ready to draw.

    Themes in the wild differ a lot: some have only "light", "dark" or "any";
    colors may be #RGB, #RRGGBB or #RRGGBBAA; many leave keys out and rely on
    the system look; translucent backgrounds expect macOS window blur. This
    normalises all of that against the built-in theme of the same appearance.
    """
    raw = BUILTIN
    path = available_themes().get(name)
    if path:
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            raw = BUILTIN
    key = "dark" if dark else "light"
    variant = raw.get(key) or raw.get("any") or raw.get("light" if dark else "dark") or {}
    variant = _normalize(variant)

    appearance = _appearance(variant, key)
    merged = _merge(BUILTIN[appearance], variant)
    notes, raw_notes = merged["folder"]["notes"], g(variant, "folder", "notes", default={})
    _drop_foreign_gradient(notes["clean"], raw_notes.get("clean"))
    for style, raw_style in zip(notes.get("colors") or [], raw_notes.get("colors") or []):
        _drop_foreign_gradient(style, raw_style)
    _flatten_backgrounds(merged, appearance)
    merged["systemAppearance"] = appearance
    return merged


def _expand_hex(value: str) -> str:
    h = value[1:]
    if len(h) in (3, 4) and all(c in "0123456789abcdefABCDEF" for c in h):
        h = "".join(c * 2 for c in h)
    if len(h) == 6:
        h += "FF"
    if len(h) == 8 and all(c in "0123456789abcdefABCDEF" for c in h):
        return "#" + h.upper()
    return value


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, str) and value.startswith("#"):
        return _expand_hex(value)
    return value


def _luminance(color: Optional[str]) -> Optional[float]:
    if not color or len(color) != 9:
        return None
    r, gr, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * gr + 0.114 * b) / 255


def _appearance(variant: dict, requested: str) -> str:
    if variant.get("systemAppearance") in ("light", "dark"):
        return variant["systemAppearance"]
    # Otherwise the note text color is the best hint: light text means a dark look.
    lum = _luminance(g(variant, "folder", "notes", "clean", "textColor"))
    if lum is not None:
        return "dark" if lum > 0.5 else "light"
    return requested


def _drop_foreign_gradient(style: dict, raw_style: Optional[dict]):
    """A theme that sets a background color but no gradient must not inherit ours."""
    if isinstance(raw_style, dict) and "backgroundColor" in raw_style \
            and "backgroundGradient" not in raw_style:
        style.pop("backgroundGradient", None)


_MATERIAL = {"light": "#EEEEF0FF", "dark": "#26272BFF"}


def _opaque(color: Optional[str], base: str) -> Optional[str]:
    if not color or len(color) != 9 or color.endswith("FF"):
        return color
    return blend_hex(color, base).upper() + "FF"


def _flatten_backgrounds(theme: dict, appearance: str):
    """macOS blurs the desktop behind translucent backgrounds; GNOME can't, so flatten
    them onto a neutral material to keep text readable."""
    material = _MATERIAL[appearance]
    for path in (("folders", "header"), ("folders", "list"), ("folder", "header"), ("folder",)):
        node = g(theme, *path)
        if isinstance(node, dict) and "backgroundColor" in node:
            node["backgroundColor"] = _opaque(node["backgroundColor"], material)
    notes = theme["folder"]["notes"]
    for style in [notes["clean"]] + list(notes.get("colors") or []):
        if "backgroundColor" in style:
            style["backgroundColor"] = _opaque(style["backgroundColor"], material)
        grad = style.get("backgroundGradient")
        if isinstance(grad, dict):
            for end in ("start", "end"):
                if end in grad:
                    grad[end] = _opaque(grad[end], material)
    field = g(theme, "search", "searchField")
    header_bg = g(theme, "folder", "header", "backgroundColor") or material
    if isinstance(field, dict) and "backgroundColor" in field:
        field["backgroundColor"] = _opaque(field["backgroundColor"], header_bg)


def _merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base.get(k), v) if k in base else v
        return out
    if isinstance(base, list) and isinstance(over, list):
        return [_merge(b, o) for b, o in zip(base, over)] + over[len(base):] + base[len(over):]
    return over if over is not None else base


def note_style(theme: dict, color_identifier: str) -> dict:
    notes = theme["folder"]["notes"]
    if color_identifier in NOTE_COLORS:
        colors = notes.get("colors") or []
        i = NOTE_COLORS.index(color_identifier)
        if i < len(colors):
            style = _merge(notes["clean"], colors[i])
            _drop_foreign_gradient(style, colors[i])
            return style
    return notes["clean"]


def g(style: dict, *keys, default=None):
    for k in keys:
        if not isinstance(style, dict) or k not in style:
            return default
        style = style[k]
    return style


def css_color(value: Optional[str], fallback: str = "transparent") -> str:
    """'#RRGGBBAA' -> 'rgba(r,g,b,a)' for GTK CSS."""
    if not value or not value.startswith("#"):
        return fallback
    h = value[1:]
    if len(h) == 6:
        h += "FF"
    if len(h) != 8:
        return fallback
    r, gr, b, a = (int(h[i:i + 2], 16) for i in (0, 2, 4, 6))
    return f"rgba({r},{gr},{b},{a / 255:.3f})"


def hex6(value: Optional[str], fallback: str = "#000000") -> str:
    """'#RRGGBBAA' -> '#RRGGBB' (for Gtk.TextTag properties, which ignore alpha)."""
    if not value or not value.startswith("#") or len(value) < 7:
        return fallback
    return value[:7]


def blend_hex(value: Optional[str], base: Optional[str], fallback: str = "#000000") -> str:
    """Flatten '#RRGGBBAA' over an opaque base color into '#RRGGBB' (Gtk.TextTag has no alpha)."""
    if not value or not value.startswith("#") or len(value) not in (7, 9):
        return fallback
    if len(value) == 7 or not base or not base.startswith("#") or len(base) < 7:
        return value[:7]
    a = int(value[7:9], 16) / 255
    mix = [round(int(value[i:i + 2], 16) * a + int(base[i:i + 2], 16) * (1 - a)) for i in (1, 3, 5)]
    return "#" + "".join(f"{c:02x}" for c in mix)


def build_css(theme: dict, font_size: int, transparent: bool = True) -> str:
    fl, fd = theme["folders"], theme["folder"]
    clean = note_style(theme, "")
    header_bg = css_color(g(fd, "header", "backgroundColor"))
    circle_bg = css_color(clean.get("backgroundColor"))
    window_bg = "transparent" if transparent else css_color(g(fd, "backgroundColor"))
    rules = [
        f"""
        .sn-window {{ background-color: {window_bg}; }}
        .sn-window * {{ outline: none; }}
        .sn-pillbar {{ background-color: {header_bg}; border-radius: 24px; padding: 6px 8px;
                       box-shadow: 0 2px 8px alpha(black, 0.18), inset 0 0 0 1px alpha(white, 0.08); }}
        .sn-pillbar .sn-title {{ color: {css_color(g(fd, 'header', 'textColor'))};
                                 font-weight: 800; font-size: {font_size + 3}pt; }}
        .sn-pillbar .sn-secondary {{ color: {css_color(g(fl, 'list', 'secondaryTextColor'))};
                                     font-size: {max(font_size - 2, 7)}pt; }}
        button.sn-circle {{ border-radius: 999px; min-width: 34px; min-height: 34px; padding: 0;
                            background-image: none; background-color: {circle_bg}; border: none;
                            color: {css_color(g(fd, 'header', 'buttonColor'))};
                            box-shadow: 0 1px 3px alpha(black, 0.12); }}
        button.sn-circle:hover {{ background-color: shade({circle_bg}, 0.95); }}
        button.sn-flat {{ background: none; border: none; box-shadow: none; padding: 2px 4px;
                          min-height: 20px; min-width: 20px; border-radius: 999px;
                          color: {css_color(g(fd, 'header', 'buttonColor'))}; }}
        button.sn-flat:hover {{ background-color: alpha(currentColor, 0.12); }}
        .sn-list {{ background-color: {css_color(g(fl, 'list', 'backgroundColor'))};
                    border-radius: 20px; box-shadow: 0 2px 8px alpha(black, 0.15); padding: 6px 0; }}
        .sn-list list, .sn-list row {{ background-color: transparent;
                    color: {css_color(g(fl, 'list', 'textColor'))}; }}
        .sn-list row {{ padding: 9px 16px; border-radius: 12px; margin: 0 6px; }}
        .sn-list row:hover, .sn-list row:selected {{
                    background-color: alpha({css_color(theme.get('accentColor'))}, 0.12); }}
        .sn-list .sn-secondary {{ color: {css_color(g(fl, 'list', 'secondaryTextColor'))};
                    font-size: {max(font_size - 1, 7)}pt; }}
        .sn-list .sn-folder-icon {{ color: {css_color(g(fl, 'cleanFolderColor'))}; }}
        .sn-scroll, .sn-scroll viewport, .sn-scroll > viewport > * {{ background-color: transparent; }}
        .sn-search {{ background-color: {css_color(g(theme, 'search', 'searchField', 'backgroundColor'))};
                      color: {css_color(g(theme, 'search', 'searchField', 'textColor'))};
                      border: none; box-shadow: none; border-radius: 16px; min-height: 30px; }}
        .sn-empty {{ color: {css_color(clean.get('tertiaryTextColor'))}; }}
        .sn-handle {{ background-color: alpha(white, 0.55); border-radius: 4px;
                      box-shadow: inset 0 0 0 1px alpha(black, 0.18); }}
        .sn-handle:hover {{ background-color: alpha(white, 0.85); }}
        """
    ]
    for ident in [""] + NOTE_COLORS:
        st = note_style(theme, ident)
        cls = f"sn-note-{ident.lower() or 'clean'}"
        grad = g(st, "backgroundGradient")
        bg = (f"linear-gradient(to bottom, {css_color(grad['start'])}, {css_color(grad['end'])})"
              if grad else "none")
        button = css_color(st.get("buttonColor"))
        rules.append(f"""
        .{cls} {{ background-color: {css_color(st.get('backgroundColor'))}; background-image: {bg};
                  border-radius: 20px; box-shadow: 0 2px 8px alpha(black, 0.16); }}
        .{cls} textview, .{cls} textview text {{ background-color: transparent;
                  color: {css_color(st.get('textColor'))}; font-size: {font_size}pt; }}
        .{cls} textview text selection {{ background-color: alpha({button}, 0.25); }}
        .{cls} button.sn-flat {{ color: {button}; }}
        .{cls} .sn-pill {{ background-color: alpha({css_color(st.get('barColor'))}, 0.75);
                  border-radius: 14px; padding: 1px 2px; }}
        .{cls} button.sn-pin {{ border-radius: 999px; min-width: 26px; min-height: 26px; padding: 0;
                  background-image: none; border: none; box-shadow: none; color: {button};
                  background-color: alpha({css_color(st.get('barColor'))}, 0.85); }}
        .{cls} .sn-meta {{ color: {css_color(st.get('tertiaryTextColor'))};
                  font-size: {max(font_size - 2, 7)}pt; }}
        .{cls} .sn-meta-icon {{ color: {css_color(st.get('tertiaryTextColor'))}; }}
        .sn-list .sn-folder-icon.sn-color-{ident.lower() or 'clean'} {{
                  color: {button if ident else css_color(g(fl, 'cleanFolderColor'))}; }}
        """)
    return "\n".join(rules)
