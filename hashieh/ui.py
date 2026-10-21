"""GTK 3 user interface modelled on SideNotes for macOS.

The window is transparent and docked to a screen edge. Inside it float a pill
header, a folder list or a column of note cards, and a pill bottom bar. Notes
are shown rendered (markup hidden, tasks as circles, colors as dots, pictures
inline) and switch to raw text editing when clicked.
"""

import math
import sys
import time
from pathlib import Path
from typing import Optional

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from . import APP_ID, APP_NAME, interop, markup, render, shortcuts, theme  # noqa: E402
from .model import NOTE_COLORS, TEXT_FORMATTINGS, Folder, Note, Store  # noqa: E402
from .paths import (DATA_DIR, DB_PATH, autostart_enabled, load_config, save_config,  # noqa: E402
                    set_autostart)

CACHE_DIR = Path(GLib.get_user_cache_dir()) / "hashieh"
NATIVE_IPC = sys.platform in ("win32", "darwin")
FORMAT_LABELS = {"standard": "Standard", "markdown": "Markdown", "monospaced": "Code"}
PLACEHOLDER = "Some new idea?"
OUTER_MARGIN = 10   # gap between the screen edge / window edge and the floating cards
CARD_PAD_X = 18
WELCOME = """# Welcome to Hashieh
This panel lives on the edge of your screen. Notes support a small Markdown subset:
**bold**, *italic*, ~~strike~~, ==underline==, ::marked::, `code`
[ ] click a circle to tick it
[x] this one is already done
Colors like #3399ff #87BC4E #ee4444 become dots.
Links: https://github.com/HAstaShakiba/hashieh
Paste or drop an image to attach it. Drop a file to link it."""


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

def _rgba(value: Optional[str], fallback=(0.5, 0.5, 0.5, 1.0)):
    if not value or not value.startswith("#") or len(value) not in (7, 9):
        return fallback
    h = value[1:] + ("FF" if len(value) == 7 else "")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4, 6))


def _cairo_pixbuf(width: int, height: int, draw) -> GdkPixbuf.Pixbuf:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    draw(cairo.Context(surface))
    surface.flush()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)


def _check_pixbuf(size: int, style: dict, done: bool) -> GdkPixbuf.Pixbuf:
    cm = style.get("checkMark") or {}

    def draw(cr):
        r = size / 2 - 1.5
        cr.arc(size / 2, size / 2, r, 0, 2 * math.pi)
        cr.set_source_rgba(*_rgba(cm.get("doneBackgroundColor" if done else "backgroundColor"),
                                  (1, 1, 1, 0.0)))
        cr.fill_preserve()
        cr.set_line_width(1.3)
        cr.set_source_rgba(*_rgba(cm.get("doneBorderColor" if done else "borderColor"),
                                  (0.6, 0.6, 0.6, 1)))
        cr.stroke()
        if done:
            cr.set_source_rgba(*_rgba(cm.get("checkMarkColor"), (0.2, 0.3, 0.6, 1)))
            cr.set_line_width(1.6)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.move_to(size * 0.30, size * 0.52)
            cr.line_to(size * 0.45, size * 0.66)
            cr.line_to(size * 0.71, size * 0.36)
            cr.stroke()

    return _cairo_pixbuf(size, size, draw)


def _dot_pixbuf(size: int, color: str) -> GdkPixbuf.Pixbuf:
    def draw(cr):
        cr.arc(size / 2, size / 2, size / 2 - 1.5, 0, 2 * math.pi)
        cr.set_source_rgba(*_rgba(color))
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.15)
        cr.set_line_width(1)
        cr.stroke()

    return _cairo_pixbuf(size, size, draw)


def _line_pixbuf(width: int, height: int, color: str) -> GdkPixbuf.Pixbuf:
    def draw(cr):
        cr.set_source_rgba(*_rgba(color, (0.5, 0.5, 0.5, 0.3)))
        cr.rectangle(0, height // 2, width, 1)
        cr.fill()

    return _cairo_pixbuf(max(width, 1), height, draw)


def _rounded_pixbuf(pixbuf: GdkPixbuf.Pixbuf, radius: float) -> GdkPixbuf.Pixbuf:
    w, h = pixbuf.get_width(), pixbuf.get_height()

    def draw(cr):
        cr.new_sub_path()
        cr.arc(w - radius, radius, radius, -math.pi / 2, 0)
        cr.arc(w - radius, h - radius, radius, 0, math.pi / 2)
        cr.arc(radius, h - radius, radius, math.pi / 2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.clip()
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()

    return _cairo_pixbuf(w, h, draw)


_picture_cache = {}


def _picture_pixbuf(store: Store, pid: str, width: int) -> Optional[GdkPixbuf.Pixbuf]:
    key = (pid, width)
    if key not in _picture_cache:
        data = store.picture(pid)
        pixbuf = None
        if data:
            try:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
                if pixbuf.get_width() > width:
                    pixbuf = pixbuf.scale_simple(
                        width, max(1, int(pixbuf.get_height() * width / pixbuf.get_width())),
                        GdkPixbuf.InterpType.BILINEAR)
                pixbuf = _rounded_pixbuf(pixbuf, 8)
            except GLib.Error:
                pixbuf = None
        _picture_cache[key] = pixbuf
    return _picture_cache[key]


def _icon_button(icon: str, tooltip: str, css: str = "sn-flat", callback=None,
                 size=Gtk.IconSize.SMALL_TOOLBAR) -> Gtk.Button:
    btn = Gtk.Button()
    btn.set_image(Gtk.Image.new_from_icon_name(icon, size))
    btn.set_tooltip_text(tooltip)
    btn.set_focus_on_click(False)
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.get_style_context().add_class(css)
    if callback:
        btn.connect("clicked", lambda b: callback(b))
    return btn


def _text_button(label: str, tooltip: str, callback=None) -> Gtk.Button:
    btn = Gtk.Button(label=label)
    btn.set_tooltip_text(tooltip)
    btn.set_focus_on_click(False)
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.get_style_context().add_class("sn-flat")
    if callback:
        btn.connect("clicked", lambda b: callback(b))
    return btn


def _menu_item(label, callback, *args, sensitive=True):
    item = Gtk.MenuItem(label=label)
    item.set_sensitive(sensitive)
    item.connect("activate", lambda *_: callback(*args))
    return item


def _check_item(label, active, callback, *args):
    item = Gtk.CheckMenuItem(label=label)
    item.set_active(active)
    item.set_draw_as_radio(True)
    item.connect("activate", lambda *_: callback(*args))
    return item


def _submenu(label: str, items) -> Gtk.MenuItem:
    item = Gtk.MenuItem(label=label)
    menu = Gtk.Menu()
    for child in items:
        menu.append(child)
    item.set_submenu(menu)
    return item


def _popup(menu: Gtk.Menu, widget: Gtk.Widget, event=None):
    menu.show_all()
    if event is not None:
        menu.popup_at_pointer(event)
    else:
        menu.popup_at_widget(widget, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None)


def _format_date(ts: float) -> str:
    return time.strftime("%d/%m/%Y, %H:%M", time.localtime(ts))


def _ask_text(parent, title: str, initial: str = "") -> Optional[str]:
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    entry = Gtk.Entry(text=initial, activates_default=True)
    for side in ("start", "end", "top", "bottom"):
        getattr(entry, f"set_margin_{side}")(12)
    dialog.get_content_area().add(entry)
    dialog.show_all()
    response = dialog.run()
    text = entry.get_text().strip()
    dialog.destroy()
    return text if response == Gtk.ResponseType.OK and text else None


def _confirm(parent, message: str) -> bool:
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True,
                               message_type=Gtk.MessageType.QUESTION,
                               buttons=Gtk.ButtonsType.OK_CANCEL, text=message)
    ok = dialog.run() == Gtk.ResponseType.OK
    dialog.destroy()
    return ok


def _info(parent, message: str, error: bool = False):
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True,
                               message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
                               buttons=Gtk.ButtonsType.OK, text=message)
    dialog.run()
    dialog.destroy()


def _open_uri(uri: str):
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error as e:
        print(f"hashieh: cannot open {uri}: {e.message}")


def _make_transparent(window: Gtk.Window) -> bool:
    screen = window.get_screen()
    visual = screen.get_rgba_visual()
    if not visual or not screen.is_composited():
        return False
    window.set_visual(visual)
    window.set_app_paintable(True)

    def clear(_w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False

    window.connect("draw", clear)
    return True


# ---------------------------------------------------------------------------------------------
# Note card
# ---------------------------------------------------------------------------------------------

class WrapTextView(Gtk.TextView):
    """A GTK 3 TextView reports its current wrapped width as its minimum, so a
    panel full of notes could never shrink. Report a small minimum instead."""

    def do_get_preferred_width(self):
        return 80, 200


class NoteCard(Gtk.EventBox):
    def __init__(self, win: "MainWindow", note: Note):
        super().__init__()
        self.win, self.store, self.note = win, win.store, note
        self.editing = False
        self.hovered = False
        self.ops = []
        self._save_id = self._highlight_id = 0
        self._loading = False
        self._color_tags = {}
        self.set_visible_window(False)
        # GtkEventBox does not draw CSS backgrounds, so the card look lives on an inner box.
        self.frame = Gtk.Box()
        self.add(self.frame)
        self._apply_color_class()

        overlay = Gtk.Overlay()
        self.frame.pack_start(overlay, True, True, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.set_margin_top(14)
        body.set_margin_bottom(8)
        body.set_margin_start(CARD_PAD_X)
        body.set_margin_end(CARD_PAD_X)
        overlay.add(body)

        self.pin_button = _icon_button("view-pin-symbolic", "Pin note", "sn-pin", self._toggle_pin)
        self.pin_button.set_halign(Gtk.Align.END)
        self.pin_button.set_valign(Gtk.Align.START)
        self.pin_button.set_margin_top(10)
        self.pin_button.set_margin_end(10)
        self.pin_button.set_no_show_all(True)
        overlay.add_overlay(self.pin_button)

        self.tags = Gtk.TextTagTable()
        self.edit_buffer = Gtk.TextBuffer(tag_table=self.tags)
        self.display_buffer = Gtk.TextBuffer(tag_table=self.tags)
        self._create_tags()
        self.edit_buffer.set_text(note.text)
        self.edit_buffer.connect("changed", self._on_changed)

        self.view = WrapTextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=True)
        self.view.set_pixels_below_lines(2)
        self.view.set_right_margin(24)  # room for the pin button
        self.view.connect("button-press-event", self._on_click)
        self.view.connect("motion-notify-event", self._on_motion)
        self.view.connect("paste-clipboard", self._on_paste)
        self.view.connect("focus-out-event", self._on_focus_out)
        self.view.drag_dest_add_uri_targets()
        self.view.connect("drag-data-received", self._on_drop)
        body.pack_start(self.view, False, False, 0)

        # Footer, shown while hovering or editing: [Aa share gear]  date  [copy trash]
        footer = Gtk.Box(spacing=6)
        left = Gtk.Box()
        left.get_style_context().add_class("sn-pill")
        left.pack_start(_text_button("Aa", "Text formatting", self._format_menu), False, False, 0)
        left.pack_start(_icon_button("emblem-shared-symbolic", "Share", callback=self._share_menu),
                        False, False, 0)
        left.pack_start(_icon_button("emblem-system-symbolic", "Note settings",
                                     callback=self._settings_menu), False, False, 0)
        footer.pack_start(left, False, False, 0)
        right = Gtk.Box()
        right.get_style_context().add_class("sn-pill")
        right.pack_start(_icon_button("edit-copy-symbolic", "Copy note", callback=lambda b: self._copy()),
                         False, False, 0)
        right.pack_start(_icon_button("edit-delete-symbolic", "Delete note",
                                      callback=lambda b: self.win.delete_note(self)), False, False, 0)
        footer.pack_end(right, False, False, 0)
        date_box = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, hexpand=True)
        cal = Gtk.Image.new_from_icon_name("x-office-calendar-symbolic", Gtk.IconSize.MENU)
        cal.get_style_context().add_class("sn-meta-icon")
        self.meta = Gtk.Label(ellipsize=Pango.EllipsizeMode.END)
        self.meta.get_style_context().add_class("sn-meta")
        date_box.pack_start(cal, False, False, 0)
        date_box.pack_start(self.meta, False, False, 0)
        footer.pack_start(date_box, True, True, 0)
        # CROSSFADE keeps the footer's space reserved. A sliding footer changes the card height
        # under the pointer, which toggles hover on and off and makes the notes jump.
        self.footer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                   transition_duration=120)
        self.footer.add(footer)
        body.pack_start(self.footer, False, False, 0)

        self._update_meta()
        self._show_display()

    # Styling ----------------------------------------------------------------
    def _style(self) -> dict:
        return theme.note_style(self.win.theme, self.note.color_identifier)

    def _apply_color_class(self):
        ctx = self.frame.get_style_context()
        for ident in [""] + NOTE_COLORS:
            ctx.remove_class(f"sn-note-{ident.lower() or 'clean'}")
        ctx.add_class(f"sn-note-{self.note.color_identifier.lower() or 'clean'}")

    def _create_tags(self):
        st = self._style()
        base = st.get("backgroundColor")

        def h(value, fallback="#000000"):
            return theme.blend_hex(value, base, fallback)

        header_color = h(theme.g(st, "header", "textColor"), h(st.get("textColor")))
        header_weight = Pango.Weight.BOLD if theme.g(st, "header", "isBold", default=True) \
            else Pango.Weight.NORMAL
        mono = "Monospace"
        specs = {
            "h1": dict(scale=1.18, weight=header_weight, foreground=header_color, pixels_below_lines=4),
            "h2": dict(scale=1.1, weight=header_weight, foreground=header_color, pixels_below_lines=3),
            "h3": dict(scale=1.0, weight=header_weight, foreground=header_color),
            "bold": dict(weight=Pango.Weight.BOLD),
            "italic": dict(style=Pango.Style.ITALIC),
            "bolditalic": dict(weight=Pango.Weight.BOLD, style=Pango.Style.ITALIC),
            "strike": dict(strikethrough=True),
            "underline": dict(underline=Pango.Underline.SINGLE),
            "mark": dict(background=h(theme.g(st, "mark", "backgroundColor"), "#f2e86d"),
                         foreground=h(theme.g(st, "mark", "textColor"), "#222222")),
            "code": dict(family=mono, scale=0.92,
                         background=h(theme.g(st, "code", "backgroundColor"), "#eeeeee"),
                         foreground=h(theme.g(st, "code", "textColor"), "#333333")),
            "codeblock": dict(family=mono, scale=0.92, left_margin=8, right_margin=8,
                              paragraph_background=h(theme.g(st, "codeBlock", "backgroundColor"),
                                                     "#eeeeee"),
                              foreground=h(theme.g(st, "codeBlock", "textColor"), "#333333")),
            "quote": dict(foreground=h(theme.g(st, "quote", "textColor"), "#777777"),
                          left_margin=12, style=Pango.Style.ITALIC),
            "link": dict(foreground=h(st.get("linkColor"), "#2f6fd6")),
            "markup": dict(foreground=h(theme.g(st, "markdownMarkup", "textColor"), "#999999")),
            "task-box": dict(family=mono, weight=Pango.Weight.BOLD,
                             foreground=h(theme.g(st, "checkMark", "borderColor"), "#999999")),
            "task-done-box": dict(family=mono, weight=Pango.Weight.BOLD,
                                  foreground=h(theme.g(st, "checkMark", "checkMarkColor"), "#3a5ba0")),
            "task-done": dict(foreground=h(st.get("secondaryTextColor"), "#777777")),
            "list-marker": dict(weight=Pango.Weight.BOLD, foreground=h(st.get("buttonColor"), "#3a5ba0")),
            "separator": dict(foreground=h(theme.g(st, "separator", "color"), "#cccccc")),
            "attachment": dict(scale=0.85, foreground=h(st.get("tertiaryTextColor"), "#999999")),
            "file-chip": dict(foreground=h(theme.g(st, "filePreview", "textColor"), "#555555"),
                              background=h(theme.g(st, "filePreview", "backgroundColor"), "#eeeeee")),
            "placeholder": dict(foreground=h(st.get("tertiaryTextColor"), "#999999")),
            "color": dict(),
        }
        for name, props in specs.items():
            existing = self.tags.lookup(name)
            if existing:
                self.tags.remove(existing)
            self.tags.add(Gtk.TextTag(name=name, **props))
        for tag in self._color_tags.values():
            self.tags.remove(tag)
        self._color_tags = {}

    def restyle(self):
        self._apply_color_class()
        self._create_tags()
        if self.editing:
            self._highlight()
        else:
            self._render_display()

    def _color_tag(self, hexcolor: str) -> Gtk.TextTag:
        key = hexcolor.lower()
        if key not in self._color_tags:
            r, g, b = (int(key[i:i + 2], 16) for i in (1, 3, 5))
            fg = "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"
            tag = Gtk.TextTag(background=key, foreground=fg, family="Monospace")
            self.tags.add(tag)
            self._color_tags[key] = tag
        return self._color_tags[key]

    def _text_width(self) -> int:
        # From the configured width, not the allocation, so pictures can't keep the window wide.
        return self.win.text_width() - 24

    def _line_px(self) -> int:
        return max(12, int(self.win.cfg["font_size"] * 96 / 72 * 1.05))

    # Display mode -----------------------------------------------------------
    def _render_display(self):
        buf, text = self.display_buffer, self.note.text
        if not text.strip():
            buf.set_text(PLACEHOLDER)
            buf.apply_tag_by_name("placeholder", *buf.get_bounds())
            self.ops = []
            return
        styles, ops = render.plan(text, self.note.text_formatting_id)
        buf.set_text(text)
        for span in styles:
            buf.apply_tag_by_name(span.tag, buf.get_iter_at_offset(span.start),
                                  buf.get_iter_at_offset(span.end))
        st, size, width = self._style(), self._line_px(), self._text_width()
        for op in reversed(ops):
            start = buf.get_iter_at_offset(op.start)
            buf.delete(start, buf.get_iter_at_offset(op.end))
            it = buf.get_iter_at_offset(op.start)
            if op.kind == "task":
                buf.insert_pixbuf(it, _check_pixbuf(size, st, op.data == "x"))
            elif op.kind == "color":
                buf.insert_pixbuf(it, _dot_pixbuf(size, op.data))
            elif op.kind == "separator":
                buf.insert_pixbuf(it, _line_pixbuf(width - 4, 10, theme.g(st, "separator", "color")))
            elif op.kind == "image":
                pixbuf = _picture_pixbuf(self.store, op.data, width - 4)
                if pixbuf:
                    buf.insert_pixbuf(it, pixbuf)
                else:
                    buf.insert_with_tags_by_name(it, "🖼", "attachment")
            elif op.kind == "file":
                label = "📁 " + op.data.rstrip("/").rsplit("/", 1)[-1]
                buf.insert_with_tags_by_name(it, label, "file-chip")
            elif op.kind == "bullet":
                buf.insert_with_tags_by_name(it, "•", "list-marker")
        self.ops = ops

    def _show_display(self):
        self._render_display()
        self.view.set_buffer(self.display_buffer)
        self.view.set_editable(False)
        self.view.set_cursor_visible(False)
        self.view.set_monospace(self.note.text_formatting_id == "monospaced")

    def start_edit(self, raw_offset: Optional[int] = None):
        if not self.editing:
            self.editing = True
            self._highlight()
            self.view.set_buffer(self.edit_buffer)
            self.view.set_editable(True)
            self.view.set_cursor_visible(True)
        if raw_offset is None:
            raw_offset = self.edit_buffer.get_char_count()
        self.edit_buffer.place_cursor(self.edit_buffer.get_iter_at_offset(raw_offset))
        self.view.grab_focus()
        self.win.current_card = self
        self._update_chrome()

    def stop_edit(self):
        if not self.editing:
            return False
        self.flush()
        self.editing = False
        self._show_display()
        self._update_chrome()
        return False

    def _on_focus_out(self, *_):
        if self.editing:
            GLib.idle_add(self._maybe_stop_edit)
        return False

    def _maybe_stop_edit(self):
        if self.editing and not self.view.has_focus():
            self.stop_edit()
        return False

    # Hover chrome -----------------------------------------------------------
    # Hover is computed by MainWindow from the pointer position: the TextView and the
    # buttons have their own GdkWindows, so per-card enter/leave events would report
    # "left the card" whenever the pointer moves over the text or the footer.
    def set_hovered(self, hovered: bool):
        if hovered != self.hovered:
            self.hovered = hovered
            self._update_chrome()

    def _update_chrome(self):
        active = self.hovered or self.editing
        self.footer.set_reveal_child(active)
        self.pin_button.set_visible(active or self.note.is_pinned)
        self.pin_button.set_opacity(1.0 if self.note.is_pinned else 0.55)

    # Editing ----------------------------------------------------------------
    def _highlight(self):
        self._highlight_id = 0
        buf = self.edit_buffer
        start, end = buf.get_bounds()
        buf.remove_all_tags(start, end)
        text = buf.get_text(start, end, True)
        for span in markup.spans(text, self.note.text_formatting_id):
            a, b = buf.get_iter_at_offset(span.start), buf.get_iter_at_offset(span.end)
            if span.tag == "color":
                buf.apply_tag(self._color_tag(span.data), a, b)
            else:
                buf.apply_tag_by_name(span.tag, a, b)
        return False

    def text(self) -> str:
        return self.edit_buffer.get_text(*self.edit_buffer.get_bounds(), True)

    def _set_text(self, text: str):
        self._loading = True
        self.edit_buffer.set_text(text)
        self._loading = False
        self.flush()
        if self.editing:
            self._highlight()
        else:
            self._render_display()

    def _on_changed(self, _buf):
        if self._loading:
            return
        if not self._highlight_id:
            self._highlight_id = GLib.idle_add(self._highlight)
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(400, self.flush)

    def flush(self):
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save_id = 0
        text = self.text()
        if text != self.note.text:
            self.note.text = text
            self.note.date = time.time()
            self.store.update_note_text(self.note.id, text)
            self._update_meta()
        return False

    def _update_meta(self):
        self.meta.set_text(_format_date(self.note.date))

    # Clicks -----------------------------------------------------------------
    def _iter_at_event(self, event):
        x, y = self.view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
        found, it = self.view.get_iter_at_location(x, y)
        return it if found else None

    def _link_at_raw(self, raw: int) -> Optional[str]:
        for span in markup.spans(self.note.text, self.note.text_formatting_id):
            if span.tag == "link" and span.start <= raw < span.end:
                return span.data
        return None

    def _on_click(self, view, event):
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != 1:
            return False
        it = self._iter_at_event(event)
        if not self.editing:
            if it is None:
                self.start_edit()
                return True
            offset = it.get_offset()
            op = render.op_at_display(self.ops, offset)
            if op and op.kind == "task":
                self._set_text(markup.toggle_task(self.note.text, op.start) or self.note.text)
                return True
            if op and op.kind == "color":
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(op.data, -1)
                self.win.toast(f"Copied {op.data}")
                return True
            if op and op.kind == "image":
                self._open_picture(op.data)
                return True
            if op and op.kind == "file":
                _open_uri(Gio.File.new_for_path(op.data).get_uri())
                return True
            raw = render.display_to_raw(self.ops, offset) if self.note.text.strip() else 0
            link = self._link_at_raw(raw)
            if link:
                _open_uri(link)
                return True
            self.start_edit(raw)
            return True
        if it is None:
            return False
        names = {t.props.name for t in it.get_tags() if t.props.name}
        if names & {"task-box", "task-done-box"}:
            toggled = markup.toggle_task(self.text(), it.get_offset())
            if toggled is not None:
                offset = self.edit_buffer.get_property("cursor-position")
                self._set_text(toggled)
                self.edit_buffer.place_cursor(self.edit_buffer.get_iter_at_offset(offset))
                return True
        if event.state & Gdk.ModifierType.CONTROL_MASK and "link" in names:
            link = self._link_at_raw(it.get_offset())
            if link:
                _open_uri(link)
                return True
        return False

    def _on_motion(self, view, event):
        it = self._iter_at_event(event)
        name = "text"
        if it is not None and not self.editing:
            op = render.op_at_display(self.ops, it.get_offset())
            tags = {t.props.name for t in it.get_tags() if t.props.name}
            name = "pointer" if (op and op.kind in ("task", "color", "image", "file")) or "link" in tags \
                else "text"
        elif it is not None:
            tags = {t.props.name for t in it.get_tags() if t.props.name}
            name = "pointer" if tags & {"task-box", "task-done-box"} else "text"
        window = view.get_window(Gtk.TextWindowType.TEXT)
        if window:
            window.set_cursor(Gdk.Cursor.new_from_name(view.get_display(), name))
        return False

    # Attachments ------------------------------------------------------------
    def _insert_snippet(self, snippet: str):
        if not self.editing:
            self.start_edit()
        it = self.edit_buffer.get_iter_at_mark(self.edit_buffer.get_insert())
        prefix = "" if it.starts_line() else "\n"
        self.edit_buffer.insert_at_cursor(prefix + snippet + "\n")

    def _add_image_bytes(self, data: bytes):
        pid = self.store.add_picture(data)
        self._insert_snippet(f"[img {pid}]")

    def _on_paste(self, view):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        if clipboard.wait_is_image_available():
            pixbuf = clipboard.wait_for_image()
            if pixbuf:
                ok, data = pixbuf.save_to_bufferv("png", [], [])
                if ok:
                    self._add_image_bytes(bytes(data))
                    view.stop_emission_by_name("paste-clipboard")
                    return
        if clipboard.wait_is_uris_available():
            uris = clipboard.wait_for_uris() or []
            if uris and all(u.startswith("file://") for u in uris):
                self._add_uris(uris)
                view.stop_emission_by_name("paste-clipboard")

    def _on_drop(self, view, context, x, y, data, info, time_):
        uris = data.get_uris()
        if not uris:
            return
        view.stop_emission_by_name("drag-data-received")
        if self.editing:
            bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, x, y)
            found, it = view.get_iter_at_location(bx, by)
            if found:
                self.edit_buffer.place_cursor(it)
        self._add_uris(uris)
        Gtk.drag_finish(context, True, False, time_)

    def _add_uris(self, uris):
        for uri in uris:
            path = Gio.File.new_for_uri(uri).get_path()
            if not path:
                self._insert_snippet(uri)
                continue
            mime, _ = Gio.content_type_guess(path, None)
            if mime and mime.startswith("image/") and Path(path).stat().st_size < 50 * 1024 * 1024:
                self._add_image_bytes(Path(path).read_bytes())
            else:
                self._insert_snippet(f"[file {path}]")

    def _open_picture(self, pid: str):
        data = self.store.picture(pid)
        if not data:
            return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / f"{pid}.{interop.image_extension(data)}"
        path.write_bytes(data)
        _open_uri(path.as_uri())

    # Footer menus -----------------------------------------------------------
    def _save_note(self):
        self.flush()
        self.store.save_note(self.note)
        self._update_meta()

    def _toggle_pin(self, _button):
        self.note.is_pinned = not self.note.is_pinned
        self._save_note()
        self.win.reload_notes(focus_note=None)

    def _format_menu(self, button):
        menu = Gtk.Menu()
        for fid in TEXT_FORMATTINGS:
            menu.append(_check_item(FORMAT_LABELS[fid], fid == self.note.text_formatting_id,
                                    self._set_format, fid))
        _popup(menu, button)

    def _set_format(self, fid: str):
        self.note.text_formatting_id = fid
        self.view.set_monospace(fid == "monospaced")
        self._save_note()
        self.restyle()

    def _share_menu(self, button):
        menu = Gtk.Menu()
        menu.append(_menu_item("Copy as text", self._copy))
        menu.append(_menu_item("Copy as plain text", self._copy_plain))
        menu.append(_menu_item("Save as Markdown file…", self._save_as))
        _popup(menu, button)

    def _settings_menu(self, button):
        menu = Gtk.Menu()
        menu.append(_submenu("Color", [
            _check_item(ident or "None", ident == self.note.color_identifier, self._set_color, ident)
            for ident in [""] + NOTE_COLORS]))
        menu.append(_submenu("Move to folder", [
            _menu_item(f.name or "Untitled", self._move, f.id)
            for f in self.store.folders() if f.id != self.note.folder_id]))
        menu.append(_menu_item("Move up", self.win.move_note, self, -1))
        menu.append(_menu_item("Move down", self.win.move_note, self, 1))
        menu.append(_menu_item("Unpin" if self.note.is_pinned else "Pin", self._toggle_pin, None))
        _popup(menu, button)

    def _set_color(self, ident: str):
        self.note.color_identifier = ident
        self._save_note()
        self.restyle()

    def _copy(self):
        self.flush()
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(self.note.text, -1)
        self.win.toast("Note copied")

    def _copy_plain(self):
        self.flush()
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(markup.to_plain(self.note.text), -1)
        self.win.toast("Note copied")

    def _save_as(self):
        self.flush()
        dialog = Gtk.FileChooserNative.new("Save note", self.win, Gtk.FileChooserAction.SAVE, None, None)
        dialog.set_current_name((markup.to_plain(self.note.title)[:40] or "note").replace("/", "-") + ".md")
        dialog.set_do_overwrite_confirmation(True)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            Path(dialog.get_filename()).write_text(self.note.text, encoding="utf-8")
        dialog.destroy()

    def _move(self, folder_id: str):
        self.note.folder_id = folder_id
        self._save_note()
        self.win.reload_notes()


# ---------------------------------------------------------------------------------------------
# Edge handle: the small bar on the screen edge that brings the panel back
# ---------------------------------------------------------------------------------------------

class EdgeHandle(Gtk.Window):
    WIDTH, HEIGHT = 7, 80

    def __init__(self, app: "HashiehApp"):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.app = app
        _make_transparent(self)
        self.set_size_request(self.WIDTH, self.HEIGHT)
        box = Gtk.EventBox()
        box.get_style_context().add_class("sn-handle")
        box.set_tooltip_text(APP_NAME)
        box.connect("button-press-event", lambda *_: app.show_panel() or True)
        box.connect("realize", lambda w: w.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "pointer")))
        self.add(box)

    def place(self):
        area = _workarea()
        x = area.x + area.width - self.WIDTH - 2 if self.app.cfg["side"] == "right" else area.x + 2
        self.move(x, area.y + (area.height - self.HEIGHT) // 2)


def _workarea() -> Gdk.Rectangle:
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    return monitor.get_workarea()


# ---------------------------------------------------------------------------------------------
# Keyboard shortcuts dialog
# ---------------------------------------------------------------------------------------------

def _event_accel(widget: Gtk.Widget, event) -> str:
    """GTK accelerator name for a key event, with Super resolved on X11."""
    state = event.state
    keymap = Gdk.Keymap.get_for_display(widget.get_display())
    state = keymap.add_virtual_modifiers(state) or state
    mods = int(state) & int(Gtk.accelerator_get_default_mod_mask())
    if mods & int(Gdk.ModifierType.SUPER_MASK):
        mods &= ~int(Gdk.ModifierType.MOD4_MASK)
    return Gtk.accelerator_name(Gdk.keyval_to_lower(event.keyval), Gdk.ModifierType(mods))


class ShortcutsDialog(Gtk.Dialog):
    def __init__(self, win: "MainWindow"):
        super().__init__(title="Keyboard Shortcuts", transient_for=win, modal=True)
        self.win, self.cfg = win, win.cfg
        self.set_keep_above(True)
        self.set_default_size(460, -1)
        self.add_button("Reset to defaults", 1)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.recording = None   # (kind, action, button)
        self._suspended = None
        self.connect("key-press-event", self._on_key)
        self.connect("response", self._on_response)

        grid = Gtk.Grid(column_spacing=8, row_spacing=6, margin=16)
        self.get_content_area().add(grid)
        self.buttons = {}
        row = 0

        def section(title, note):
            nonlocal row
            head = Gtk.Label(xalign=0)
            head.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
            head.set_margin_top(6 if row else 0)
            grid.attach(head, 0, row, 3, 1)
            sub = Gtk.Label(label=note, xalign=0, wrap=True, max_width_chars=60)
            sub.get_style_context().add_class("dim-label")
            grid.attach(sub, 0, row + 1, 3, 1)
            row += 2

        def add(kind, action, title, accel):
            nonlocal row
            grid.attach(Gtk.Label(label=title, xalign=0, hexpand=True), 0, row, 1, 1)
            btn = Gtk.Button(label=shortcuts.label(accel))
            btn.set_size_request(150, -1)
            btn.set_tooltip_text("Click, then press the new keys. Esc cancels, Backspace disables.")
            btn.connect("clicked", lambda b: self._start(kind, action, b))
            grid.attach(btn, 1, row, 1, 1)
            edit = Gtk.Button.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON)
            edit.set_tooltip_text("Type the shortcut (for keys the desktop grabs, e.g. <Super>n)")
            edit.connect("clicked", lambda b: self._type(kind, action))
            grid.attach(edit, 2, row, 1, 1)
            self.buttons[(kind, action)] = btn
            row += 1

        if shortcuts.global_available():
            section("Global", shortcuts.global_scope())
            for action, (title, _, _) in shortcuts.GLOBAL_ACTIONS.items():
                add("global", action, title, shortcuts.get_global(action))
        else:
            section("Global", "Not available on this desktop. "
                              "Bind your desktop's own shortcut to: hashieh --toggle")
        section(f"In {APP_NAME}", "Work while the panel has focus. Esc always goes back or hides.")
        for action, title in shortcuts.APP_ACTIONS.items():
            add("app", action, title, self._app_value(action))
        self.show_all()

    def _app_value(self, action: str) -> str:
        return self.cfg.setdefault("shortcuts", {}).get(action, shortcuts.DEFAULT_APP[action])

    def _current(self, kind: str, action: str) -> str:
        return shortcuts.get_global(action) if kind == "global" else self._app_value(action)

    # Recording ----------------------------------------------------------------
    def _start(self, kind, action, button):
        self._stop()
        self.recording = (kind, action, button)
        button.set_label("Press keys…")
        if kind == "global":
            # Otherwise pressing the current global key would toggle the panel instead.
            self._suspended = shortcuts.suspend_globals()

    def _stop(self):
        if self._suspended is not None:
            shortcuts.restore_globals(self._suspended)
            self._suspended = None
        if self.recording:
            kind, action, button = self.recording
            button.set_label(shortcuts.label(self._current(kind, action)))
            self.recording = None

    def _on_key(self, _w, event):
        if not self.recording:
            return False
        kind, action, _ = self.recording
        name = Gdk.keyval_name(event.keyval) or ""
        mods = event.state & Gtk.accelerator_get_default_mod_mask()
        if event.is_modifier:
            return True
        if name == "Escape" and not mods:
            self._stop()
            return True
        accel = "" if name == "BackSpace" and not mods else _event_accel(self, event)
        self._stop()
        self._apply(kind, action, accel)
        return True

    def _type(self, kind, action):
        self._stop()
        text = _ask_text(self, "Shortcut (e.g. <Super>n, <Control><Alt>s; empty disables)",
                         self._current(kind, action))
        if text is not None:
            self._apply(kind, action, text)

    # Saving -------------------------------------------------------------------
    def _apply(self, kind: str, action: str, accel: str):
        error = shortcuts.validate(accel)
        if error:
            _info(self, error, error=True)
            return
        app_map = dict(self.cfg.get("shortcuts", {}))
        for a, default in shortcuts.DEFAULT_APP.items():
            app_map.setdefault(a, default)
        if kind == "app":
            app_map.pop(action, None)
        conflicts = shortcuts.find_conflicts(accel, ignore_action=action if kind == "global" else None,
                                             app_shortcuts=app_map)
        if any(c.schema_id == "os" for c in conflicts):
            _info(self, f"{shortcuts.label(accel)} is already used by another program or by the "
                        "system. Please choose another shortcut.", error=True)
            return
        if conflicts:
            listing = "\n".join(f"• {c.description}" for c in conflicts)
            if not _confirm(self, f"{shortcuts.label(accel)} is already used by:\n{listing}\n\n"
                                  "Remove it there and use it here?"):
                return
            for c in conflicts:
                shortcuts.free(c, accel, self.cfg.setdefault("shortcuts", {}))
        if kind == "global":
            if not shortcuts.set_global(action, accel):
                _info(self, f"The system didn't accept {shortcuts.label(accel)}. "
                            "Please choose another shortcut.", error=True)
        else:
            self.cfg.setdefault("shortcuts", {})[action] = accel
        save_config(self.cfg)
        self._refresh()

    def _refresh(self):
        for (kind, action), btn in self.buttons.items():
            btn.set_label(shortcuts.label(self._current(kind, action)))

    def _on_response(self, _d, response):
        self._stop()
        if response == 1:
            if _confirm(self, "Reset the in-app shortcuts to their defaults?"):
                self.cfg["shortcuts"] = dict(shortcuts.DEFAULT_APP)
                save_config(self.cfg)
                self._refresh()
            return
        self.destroy()


# ---------------------------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------------------------

class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: "HashiehApp"):
        super().__init__(application=app, title=APP_NAME)
        self.app, self.store, self.cfg = app, app.store, app.cfg
        self.folder: Optional[Folder] = None
        self.cards = []
        self.current_card: Optional[NoteCard] = None
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.theme = {}
        self._toast_id = 0
        docked = self.cfg["dock"]
        self.transparent = _make_transparent(self) if docked else False

        self.set_decorated(not docked)
        self.set_skip_taskbar_hint(docked)
        self.set_skip_pager_hint(docked)
        self.set_keep_above(self.cfg["keep_above"])
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY if docked else Gdk.WindowTypeHint.NORMAL)
        self.set_icon_name("accessories-text-editor")
        self.get_style_context().add_class("sn-window")

        outer = Gtk.Box()
        self.add(outer)
        self.grip = Gtk.EventBox()
        self.grip.set_size_request(6, -1)
        self.grip.set_visible_window(False)
        # A windowless EventBox shares the toplevel's GdkWindow, so setting the cursor at
        # realize time would turn the whole panel into a resize cursor. Swap it on hover only.
        self.grip.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.grip.connect("enter-notify-event", lambda w, e: self.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "ew-resize")))
        self.grip.connect("leave-notify-event", lambda w, e: self.get_window().set_cursor(None))
        self.grip.connect("button-press-event", self._on_grip)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main.set_margin_top(OUTER_MARGIN)
        main.set_margin_bottom(OUTER_MARGIN)
        main.set_margin_start(OUTER_MARGIN - 4 if self.cfg["side"] == "right" else OUTER_MARGIN)
        main.set_margin_end(OUTER_MARGIN if self.cfg["side"] == "right" else OUTER_MARGIN - 4)
        if self.cfg["side"] == "right":
            outer.pack_start(self.grip, False, False, 0)
            outer.pack_start(main, True, True, 0)
        else:
            outer.pack_start(main, True, True, 0)
            outer.pack_start(self.grip, False, False, 0)

        # Header pill: (‹)  Title          (search) (+)
        header = Gtk.Box(spacing=8)
        header.get_style_context().add_class("sn-pillbar")
        self.back_button = _icon_button("go-previous-symbolic", "Back to folders (Esc)", "sn-circle",
                                        lambda b: self.show_folders())
        self.title_label = Gtk.Label(label=APP_NAME, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title_label.get_style_context().add_class("sn-title")
        header.pack_start(self.back_button, False, False, 0)
        header.pack_start(self.title_label, True, True, 4)
        header.pack_end(_icon_button("list-add-symbolic", "New (Ctrl+N)", "sn-circle",
                                     lambda b: self.new_item()), False, False, 0)
        header.pack_end(_icon_button("system-search-symbolic", "Search (Ctrl+F)", "sn-circle",
                                     lambda b: self.toggle_search()), False, False, 0)
        main.pack_start(header, False, False, 0)

        self.search_entry = Gtk.SearchEntry(placeholder_text="Search notes")
        self.search_entry.get_style_context().add_class("sn-search")
        self.search_entry.connect("search-changed", self._on_search)
        self.search_revealer = Gtk.Revealer()
        self.search_revealer.add(self.search_entry)
        main.pack_start(self.search_revealer, False, False, 0)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=150)
        main.pack_start(self.stack, True, True, 0)

        self.folder_list = Gtk.ListBox()
        self.folder_list.connect("row-activated", lambda lb, row: self.open_folder(row.folder))
        self.folder_list.connect("button-press-event", self._on_folder_click)
        self.stack.add_named(self._card_list(self.folder_list), "folders")

        self.notes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.notes_box.set_margin_start(2)
        self.notes_box.set_margin_end(2)
        self.notes_box.set_margin_top(2)
        self.notes_box.set_margin_bottom(6)
        self.notes_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.notes_scroll.set_overlay_scrolling(True)
        self.notes_scroll.get_style_context().add_class("sn-scroll")
        self.notes_scroll.add(self.notes_box)
        self.stack.add_named(self.notes_scroll, "notes")

        self.results = Gtk.ListBox()
        self.results.connect("row-activated", lambda lb, row: self._open_result(row.note))
        self.stack.add_named(self._card_list(self.results), "search")

        # Bottom pill: (⋯)   12 notes   (⚙)
        bottom = Gtk.Box(spacing=4)
        bottom.get_style_context().add_class("sn-pillbar")
        bottom.pack_start(_icon_button("view-more-horizontal-symbolic", "More", callback=self._more_menu),
                          False, False, 0)
        self.status_label = Gtk.Label()
        self.status_label.get_style_context().add_class("sn-secondary")
        bottom.set_center_widget(self.status_label)
        bottom.pack_end(_icon_button("emblem-system-symbolic", "Settings", callback=self._settings_menu),
                        False, False, 0)
        main.pack_end(bottom, False, False, 0)

        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("motion-notify-event", lambda *_: self.update_hover())
        self.connect("leave-notify-event", self._on_window_leave)
        self.notes_scroll.get_vadjustment().connect("value-changed", lambda *_: self.update_hover())
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_delete)
        self.connect("configure-event", self._on_configure)
        self.apply_theme()
        self.dock()
        # Gtk.Stack ignores set_visible_child() for children that are not shown yet.
        outer.show_all()

        last = self.cfg.get("last_folder") and self.store.folder(self.cfg["last_folder"])
        self.show_folders()
        if last:
            self.open_folder(last)

    @staticmethod
    def _card_list(listbox: Gtk.ListBox) -> Gtk.Widget:
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.get_style_context().add_class("sn-scroll")
        sw.add(listbox)
        frame = Gtk.Box()
        frame.get_style_context().add_class("sn-list")
        frame.set_margin_start(2)
        frame.set_margin_end(2)
        frame.pack_start(sw, True, True, 0)
        return frame

    def text_width(self) -> int:
        return int(self.cfg["width"]) - 2 * OUTER_MARGIN - 4 - 2 * CARD_PAD_X - 6

    # Geometry ---------------------------------------------------------------
    def dock(self):
        width = int(self.cfg["width"])
        area = _workarea()
        if not self.cfg["dock"]:
            self.set_default_size(width, int(area.height * 0.8))
            return
        self.set_size_request(260, -1)
        self.resize(width, area.height)
        x = area.x + area.width - width if self.cfg["side"] == "right" else area.x
        self.move(x, area.y)

    def _on_grip(self, widget, event):
        if event.button == 1:
            edge = Gdk.WindowEdge.WEST if self.cfg["side"] == "right" else Gdk.WindowEdge.EAST
            self.begin_resize_drag(edge, event.button, int(event.x_root), int(event.y_root), event.time)
        return True

    def _on_configure(self, _w, event):
        if self.cfg["dock"] and abs(event.width - self.cfg["width"]) > 2 and self.get_visible():
            self.cfg["width"] = event.width
            GLib.timeout_add(300, self._persist_width)
        return False

    def _persist_width(self):
        save_config(self.cfg)
        self.dock()
        _picture_cache.clear()
        for card in self.cards:
            if not card.editing:
                card._render_display()
        return False

    # Hover ----------------------------------------------------------------
    def update_hover(self, outside: bool = False):
        hovered = None
        window = self.get_window()
        if not outside and window and self.cards:
            pointer = self.get_display().get_default_seat().get_pointer()
            _, x, y, _ = window.get_device_position(pointer)
            hovered = self.card_at(x, y)
        for card in self.cards:
            card.set_hovered(card is hovered)
        return False

    def card_at(self, x: int, y: int) -> Optional[NoteCard]:
        """The card under window coordinates (x, y), ignoring cards scrolled out of view."""
        origin = self.notes_scroll.translate_coordinates(self, 0, 0)
        sa = self.notes_scroll.get_allocation()
        if not origin or not (origin[0] <= x < origin[0] + sa.width and origin[1] <= y < origin[1] + sa.height):
            return None
        for card in self.cards:
            coords = card.translate_coordinates(self, 0, 0)
            a = card.get_allocation()
            if coords and coords[0] <= x < coords[0] + a.width and coords[1] <= y < coords[1] + a.height:
                return card
        return None

    def _on_window_leave(self, _w, event):
        # INFERIOR means the pointer only moved into a child window of the panel.
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.update_hover(outside=True)
        return False

    def _on_delete(self, *_):
        self.app.hide_panel()
        return True

    # Theme ------------------------------------------------------------------
    def is_dark(self) -> bool:
        mode = self.cfg["appearance"]
        if mode in ("light", "dark"):
            return mode == "dark"
        if sys.platform == "win32":
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                    return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
            except OSError:
                return False
        if sys.platform == "darwin":
            import subprocess
            try:
                out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                     capture_output=True, text=True, timeout=2).stdout
            except (OSError, subprocess.SubprocessError):
                return False
            return out.strip().lower() == "dark"
        source = Gio.SettingsSchemaSource.get_default()
        if source and source.lookup("org.gnome.desktop.interface", True):
            settings = Gio.Settings.new("org.gnome.desktop.interface")
            if "color-scheme" in settings.list_keys() and settings.get_string("color-scheme") == "prefer-dark":
                return True
            return settings.get_string("gtk-theme").lower().endswith("dark")
        return False

    def apply_theme(self):
        dark = self.is_dark()
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", dark)
        self.theme = theme.load(self.cfg["theme"], dark)
        css = theme.build_css(self.theme, int(self.cfg["font_size"]), self.transparent)
        self.css.load_from_data(css.encode())
        for card in self.cards:
            card.restyle()

    # Navigation -------------------------------------------------------------
    def flush_all(self):
        for card in self.cards:
            card.flush()

    def show_folders(self):
        self.flush_all()
        self.folder = None
        self.cards = []
        self.cfg["last_folder"] = None
        save_config(self.cfg)
        self.title_label.set_text(APP_NAME)
        self.back_button.set_visible(False)
        self.reload_folders()
        self.stack.set_visible_child_name("folders")

    def reload_folders(self):
        for row in self.folder_list.get_children():
            self.folder_list.remove(row)
        folders = self.store.folders()
        for folder in folders:
            row = Gtk.ListBoxRow()
            row.folder = folder
            box = Gtk.Box(spacing=10)
            icon = Gtk.Image.new_from_icon_name("folder-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
            icon.get_style_context().add_class("sn-folder-icon")
            icon.get_style_context().add_class(f"sn-color-{folder.color_identifier.lower() or 'clean'}")
            box.pack_start(icon, False, False, 0)
            box.pack_start(Gtk.Label(label=folder.name or "Untitled", xalign=0,
                                     ellipsize=Pango.EllipsizeMode.END), True, True, 0)
            count = Gtk.Label(label=str(self.store.note_count(folder.id)))
            count.get_style_context().add_class("sn-secondary")
            box.pack_end(count, False, False, 0)
            if folder.is_pinned:
                pin = Gtk.Image.new_from_icon_name("view-pin-symbolic", Gtk.IconSize.MENU)
                pin.get_style_context().add_class("sn-secondary")
                box.pack_end(pin, False, False, 0)
            row.add(box)
            self.folder_list.add(row)
        empty = Gtk.Label(label="No folders yet.\nClick + to create one.", justify=Gtk.Justification.CENTER)
        empty.get_style_context().add_class("sn-empty")
        empty.set_margin_top(24)
        empty.show()
        self.folder_list.set_placeholder(empty)
        self.folder_list.show_all()
        self.status_label.set_text(f"{len(folders)} folder{'s' if len(folders) != 1 else ''}")

    def open_folder(self, folder: Folder, focus_note: Optional[str] = None):
        self.flush_all()
        self.folder = folder
        folder.last_open = time.time()
        self.store.save_folder(folder)
        self.cfg["last_folder"] = folder.id
        save_config(self.cfg)
        self.title_label.set_text(folder.name or "Untitled")
        self.back_button.set_visible(True)
        self.search_revealer.set_reveal_child(False)
        self.reload_notes(focus_note)
        self.stack.set_visible_child_name("notes")

    def reload_notes(self, focus_note: Optional[str] = None, edit: bool = False):
        if not self.folder:
            return
        self.flush_all()
        for child in self.notes_box.get_children():
            self.notes_box.remove(child)
        self.current_card = None
        self.cards = [NoteCard(self, n) for n in self.store.notes(self.folder.id)]
        for card in self.cards:
            self.notes_box.pack_start(card, False, False, 0)
        if not self.cards:
            empty = Gtk.Label(label="This folder is empty.\nClick + to add a note.",
                              justify=Gtk.Justification.CENTER)
            empty.get_style_context().add_class("sn-empty")
            self.notes_box.pack_start(empty, False, False, 24)
        self.notes_box.show_all()
        for card in self.cards:
            card._update_chrome()
            if card.note.id == focus_note:
                GLib.idle_add(self._focus_card, card, edit)
        n = len(self.cards)
        self.status_label.set_text(f"{n} note{'s' if n != 1 else ''}")

    def _focus_card(self, card: NoteCard, edit: bool):
        if edit:
            card.start_edit()
        alloc = card.get_allocation()
        self.notes_scroll.get_vadjustment().set_value(max(alloc.y - 8, 0))
        return False

    # Actions ----------------------------------------------------------------
    def new_item(self, text: str = ""):
        if self.folder is None:
            name = _ask_text(self, "New folder")
            if name:
                self.open_folder(self.store.create_folder(name))
            return
        note = self.store.create_note(self.folder.id, text, self.cfg["default_formatting"])
        self.reload_notes(focus_note=note.id, edit=True)

    def new_note_from_cli(self, text: str):
        folder = self.folder or (self.store.folders() or [None])[0] or self.store.create_folder("Notes")
        if self.folder is None or self.folder.id != folder.id:
            self.open_folder(folder)
        note = self.store.create_note(folder.id, text, self.cfg["default_formatting"])
        self.reload_notes(focus_note=note.id, edit=not text)

    def delete_note(self, card: NoteCard):
        preview = markup.to_plain(card.note.title)[:40] or "this note"
        if _confirm(self, f"Delete “{preview}”?"):
            card.flush()
            self.store.delete_note(card.note.id)
            self.reload_notes()

    def move_note(self, card: NoteCard, delta: int):
        ids = [c.note.id for c in self.cards]
        i = ids.index(card.note.id)
        j = max(0, min(len(ids) - 1, i + delta))
        ids.insert(j, ids.pop(i))
        self.store.reorder_notes(ids)
        self.reload_notes(focus_note=card.note.id)

    def toggle_search(self):
        reveal = not self.search_revealer.get_reveal_child()
        self.search_revealer.set_reveal_child(reveal)
        if reveal:
            self.search_entry.grab_focus()
        else:
            self.search_entry.set_text("")
            self.stack.set_visible_child_name("notes" if self.folder else "folders")

    def _on_search(self, entry):
        query = entry.get_text().strip()
        if not query:
            self.stack.set_visible_child_name("notes" if self.folder else "folders")
            return
        self.flush_all()
        for row in self.results.get_children():
            self.results.remove(row)
        names = {f.id: f.name for f in self.store.folders(include_archived=True)}
        found = self.store.search(query)
        for note in found:
            row = Gtk.ListBoxRow()
            row.note = note
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title = Gtk.Label(label=markup.to_plain(note.title) or "Untitled", xalign=0,
                              ellipsize=Pango.EllipsizeMode.END)
            title.set_markup(f"<b>{GLib.markup_escape_text(title.get_text())}</b>")
            body = " ".join(markup.to_plain(note.text).split())[:140]
            label = Gtk.Label(label=body, xalign=0, wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END)
            label.set_max_width_chars(30)
            where = Gtk.Label(label=names.get(note.folder_id, "?"), xalign=0)
            where.get_style_context().add_class("sn-secondary")
            for w in (title, label, where):
                box.pack_start(w, False, False, 0)
            row.add(box)
            self.results.add(row)
        self.results.show_all()
        self.status_label.set_text(f"{len(found)} result{'s' if len(found) != 1 else ''}")
        self.stack.set_visible_child_name("search")

    def _open_result(self, note: Note):
        folder = self.store.folder(note.folder_id)
        if folder:
            self.search_entry.set_text("")
            self.open_folder(folder, focus_note=note.id)

    def _folder_menu_items(self, folder: Folder):
        return [
            _menu_item("Rename…", self._rename_folder, folder),
            _menu_item("Unpin" if folder.is_pinned else "Pin", self._pin_folder, folder),
            _submenu("Color", [_check_item(ident or "None", ident == folder.color_identifier,
                                           self._color_folder, folder, ident)
                               for ident in [""] + NOTE_COLORS]),
            Gtk.SeparatorMenuItem(),
            _menu_item("Delete folder…", self._delete_folder, folder),
        ]

    def _on_folder_click(self, listbox, event):
        if event.button != 3:
            return False
        row = listbox.get_row_at_y(int(event.y))
        if not row:
            return False
        menu = Gtk.Menu()
        for item in self._folder_menu_items(row.folder):
            menu.append(item)
        _popup(menu, listbox, event)
        return True

    def _more_menu(self, button):
        menu = Gtk.Menu()
        if self.folder:
            menu.append(_menu_item("New note", self.new_item))
            for item in self._folder_menu_items(self.folder):
                menu.append(item)
        else:
            menu.append(_menu_item("New folder…", self.new_item))
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(_menu_item("Import SideNotes data…", self._import))
        menu.append(_menu_item("Export to folder…", self._export))
        _popup(menu, button)

    def _rename_folder(self, folder: Folder):
        name = _ask_text(self, "Rename folder", folder.name)
        if name:
            folder.name = name
            self.store.save_folder(folder)
            if self.folder and self.folder.id == folder.id:
                self.title_label.set_text(name)
            else:
                self.reload_folders()

    def _pin_folder(self, folder: Folder):
        folder.is_pinned = not folder.is_pinned
        self.store.save_folder(folder)
        if not self.folder:
            self.reload_folders()

    def _color_folder(self, folder: Folder, ident: str):
        folder.color_identifier = ident
        self.store.save_folder(folder)
        if not self.folder:
            self.reload_folders()

    def _delete_folder(self, folder: Folder):
        count = self.store.note_count(folder.id)
        if _confirm(self, f"Delete folder “{folder.name}” and its {count} note(s)?"):
            self.store.delete_folder(folder.id)
            self.show_folders()

    def _settings_menu(self, button):
        menu = Gtk.Menu()
        menu.append(_submenu("Theme", [_check_item(name, name == self.cfg["theme"], self._set_cfg, "theme", name)
                                       for name in theme.available_themes()]))
        menu.append(_submenu("Appearance", [
            _check_item(mode.title(), mode == self.cfg["appearance"], self._set_cfg, "appearance", mode)
            for mode in ("auto", "light", "dark")]))
        menu.append(_submenu("New note formatting", [
            _check_item(FORMAT_LABELS[f], f == self.cfg["default_formatting"],
                        self._set_cfg, "default_formatting", f) for f in TEXT_FORMATTINGS]))
        menu.append(_submenu("Window", [
            _check_item("Right edge", self.cfg["side"] == "right", self._set_cfg, "side", "right"),
            _check_item("Left edge", self.cfg["side"] == "left", self._set_cfg, "side", "left"),
            Gtk.SeparatorMenuItem(),
            _menu_item("Floating window" if self.cfg["dock"] else "Dock to screen edge",
                       self._set_cfg, "dock", not self.cfg["dock"]),
            _menu_item(("✓ " if self.cfg["keep_above"] else "") + "Always on top",
                       self._set_cfg, "keep_above", not self.cfg["keep_above"]),
            _menu_item(("✓ " if self.cfg["show_handle"] else "") + "Show edge handle when hidden",
                       self._set_cfg, "show_handle", not self.cfg["show_handle"]),
        ]))
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(_menu_item(("✓ " if autostart_enabled() else "") + "Start at login",
                               lambda: set_autostart(not autostart_enabled(), shortcuts.launcher())))
        menu.append(_menu_item("Keyboard shortcuts…", lambda: ShortcutsDialog(self)))
        menu.append(_menu_item("Install themes…", self._install_themes))
        menu.append(_menu_item("Open data folder", lambda: _open_uri(DATA_DIR.as_uri())))
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(_menu_item("Hide (Esc)", self.app.hide_panel))
        menu.append(_menu_item("Quit (Ctrl+Q)", self.app.quit_app))
        _popup(menu, button)

    def _set_cfg(self, key, value):
        self.cfg[key] = value
        save_config(self.cfg)
        if key in ("theme", "appearance", "font_size"):
            self.apply_theme()
        elif key in ("side", "dock"):
            self.app.rebuild_window()
        elif key == "keep_above":
            self.set_keep_above(value)

    def _choose(self, title, action, filters=()):
        dialog = Gtk.FileChooserNative.new(title, self, action, None, None)
        for name, patterns in filters:
            f = Gtk.FileFilter()
            f.set_name(name)
            for p in patterns:
                f.add_pattern(p)
            dialog.add_filter(f)
        path = dialog.get_filename() if dialog.run() == Gtk.ResponseType.ACCEPT else None
        dialog.destroy()
        return Path(path) if path else None

    def _import(self):
        path = self._choose("Import SideNotes data", Gtk.FileChooserAction.OPEN, [
            ("SideNotes data", ["*.json", "*.sqlite", "*.snbackup", "*.zip"]), ("All files", ["*"])])
        if not path:
            return
        try:
            stats = interop.import_any(self.store, path)
        except Exception as e:  # report any parser failure to the user
            _info(self, f"Import failed: {e}", error=True)
            return
        _info(self, f"Imported {stats}.")
        if self.folder:
            self.reload_notes()
        else:
            self.reload_folders()

    def _install_themes(self):
        path = self._choose("Choose a .sntheme file or the SideNotes.app folder",
                            Gtk.FileChooserAction.SELECT_FOLDER)
        if path:
            try:
                names = theme.install_themes(path)
                _info(self, f"Installed {len(names)} theme(s).")
            except Exception as e:  # report any parser failure to the user
                _info(self, f"Theme install failed: {e}", error=True)

    def _export(self):
        path = self._choose("Export to folder", Gtk.FileChooserAction.SELECT_FOLDER)
        if path:
            self.flush_all()
            out = interop.export_json(self.store, path / "SideNotes Export")
            _info(self, f"Exported to {out.parent}")

    def toast(self, text: str):
        self.status_label.set_text(text)
        if self._toast_id:
            GLib.source_remove(self._toast_id)
        self._toast_id = GLib.timeout_add(1500, self._restore_status)

    def _restore_status(self):
        self._toast_id = 0
        if self.folder:
            n = len(self.cards)
            self.status_label.set_text(f"{n} note{'s' if n != 1 else ''}")
        else:
            n = len(self.folder_list.get_children())
            self.status_label.set_text(f"{n} folder{'s' if n != 1 else ''}")
        return False

    def _shortcut_action(self, event) -> Optional[str]:
        pressed = shortcuts.parse(_event_accel(self, event))
        if not pressed:
            return None
        configured = self.cfg.get("shortcuts", {})
        for action, default in shortcuts.DEFAULT_APP.items():
            if shortcuts.parse(configured.get(action, default)) == pressed:
                return action
        # Keypad and shifted variants of the zoom keys always work.
        name = Gdk.keyval_name(event.keyval) or ""
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            return {"KP_Add": "zoom_in", "plus": "zoom_in", "KP_Subtract": "zoom_out"}.get(name)
        return None

    def _on_key(self, _w, event):
        key = Gdk.keyval_name(event.keyval) or ""
        if key == "Escape":
            editing = next((c for c in self.cards if c.editing), None)
            if editing:
                editing.stop_edit()
                self.set_focus(None)
            elif self.search_revealer.get_reveal_child():
                self.toggle_search()
            elif self.folder:
                self.show_folders()
            else:
                self.app.hide_panel()
            return True
        action = self._shortcut_action(event)
        if action == "new_note":
            self.new_item()
        elif action == "search":
            self.toggle_search()
        elif action == "hide":
            self.app.hide_panel()
        elif action == "quit":
            self.app.quit_app()
        elif action == "zoom_in":
            self._set_cfg("font_size", min(int(self.cfg["font_size"]) + 1, 30))
        elif action == "zoom_out":
            self._set_cfg("font_size", max(int(self.cfg["font_size"]) - 1, 7))
        elif action == "delete_note" and self.current_card and self.current_card in self.cards:
            self.delete_note(self.current_card)
        else:
            return False
        return True


# ---------------------------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------------------------

class HashiehApp(Gtk.Application):
    def __init__(self, selftest_dir: Optional[str] = None):
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        if NATIVE_IPC:
            # No session bus on Windows/macOS: single instance is handled by ipc.py instead.
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
        self.window: Optional[MainWindow] = None
        self.handle: Optional[EdgeHandle] = None
        self.store: Optional[Store] = None
        self.hotkeys = None
        self._ipc = None
        self.selftest_dir = selftest_dir
        self.cfg = load_config()
        self.add_main_option("toggle", ord("t"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Show or hide the panel", None)
        self.add_main_option("show", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Show the panel", None)
        self.add_main_option("hide", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Hide the panel", None)
        self.add_main_option("new-note", ord("n"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Create a note with TEXT", "TEXT")
        self.add_main_option("new-note-from-clipboard", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Create a note from the clipboard text", None)
        self.add_main_option("quit", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Quit", None)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        shortcuts.migrate_legacy()
        self.store = Store(DB_PATH)
        if self.store.is_empty():
            folder = self.store.create_folder("Notes")
            self.store.create_note(folder.id, WELCOME, "markdown")
        if NATIVE_IPC and not self.selftest_dir:
            from . import ipc
            self._ipc = ipc.Server(lambda argv: self.handle_options(ipc.parse_argv(argv)))

    def do_command_line(self, command_line):
        self.handle_options(command_line.get_options_dict().end().unpack())
        return 0

    def handle_options(self, opts: dict):
        if opts.get("quit"):
            self.quit_app()
            return
        first = self.window is None
        if first:
            self.window = MainWindow(self)
            self._watch_appearance()
            self._setup_global_shortcuts()
            if self.selftest_dir:
                GLib.idle_add(self._run_selftest)
                return
        if opts.get("hide"):
            self.hide_panel()
        elif opts.get("toggle") and not first and self.window.get_visible():
            self.hide_panel()
        else:
            self.show_panel()
        if "new-note" in opts:
            self.show_panel()
            self.window.new_note_from_cli(opts["new-note"])
        if opts.get("new-note-from-clipboard"):
            self._new_note_from_clipboard()

    def _setup_global_shortcuts(self):
        if NATIVE_IPC:
            from . import hotkeys
            self.hotkeys = hotkeys.create(self._on_hotkey)
            shortcuts.bind_runtime(self.cfg, self.hotkeys)
        shortcuts.first_run_setup(self.cfg)

    def _on_hotkey(self, action: str):
        if action == "toggle":
            if self.window.get_visible():
                self.hide_panel()
            else:
                self.show_panel()
        elif action == "new_note":
            self._new_note_from_clipboard()

    def _new_note_from_clipboard(self):
        text = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() or ""
        self.show_panel()
        self.window.new_note_from_cli(text)

    def show_panel(self):
        win = self.window
        win.show()
        win.back_button.set_visible(win.folder is not None)
        win.dock()
        win.present_with_time(Gtk.get_current_event_time() or GLib.get_monotonic_time() // 1000)
        if self.handle:
            self.handle.hide()

    def hide_panel(self):
        if self.window:
            for card in self.window.cards:
                card.stop_edit()
            self.window.hide()
        if self.cfg["dock"] and self.cfg["show_handle"]:
            if self.handle is None:
                self.handle = EdgeHandle(self)
            self.handle.show_all()
            self.handle.place()

    def _watch_appearance(self):
        source = Gio.SettingsSchemaSource.get_default()
        if source and source.lookup("org.gnome.desktop.interface", True):
            self._iface = Gio.Settings.new("org.gnome.desktop.interface")
            self._iface.connect("changed", lambda s, key: key in ("color-scheme", "gtk-theme")
                                and self.window and self.window.apply_theme())
        elif sys.platform in ("win32", "darwin"):
            # No change notification there; re-check now and then while following the system.
            self._last_dark = None

            def poll():
                if self.window and self.cfg["appearance"] == "auto":
                    dark = self.window.is_dark()
                    if self._last_dark is not None and dark != self._last_dark:
                        self.window.apply_theme()
                    self._last_dark = dark
                return True
            GLib.timeout_add_seconds(20, poll)

    def rebuild_window(self):
        old = self.window
        old.flush_all()
        folder = old.folder
        self.window = MainWindow(self)
        old.destroy()
        if self.handle:
            self.handle.destroy()
            self.handle = None
        if folder:
            self.window.open_folder(folder)
        self.show_panel()

    def quit_app(self):
        if self.window:
            self.window.flush_all()
        self.quit()

    def do_shutdown(self):
        if self.window:
            self.window.flush_all()
        if self.hotkeys:
            self.hotkeys.unregister_all()
        if self._ipc:
            self._ipc.close()
        if self.store:
            self.store.close()
        Gtk.Application.do_shutdown(self)

    # Self-test: used by CI on every platform to check a build really works ----------------------
    def _run_selftest(self):
        import json
        import platform

        win = self.window
        folder = self.store.folders()[0]
        sample = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 320, 120)
        sample.fill(0x3B6FD8FF)
        _, data = sample.save_to_bufferv("png", [], [])
        pid = self.store.add_picture(bytes(data))
        note = self.store.create_note(folder.id, "# Self-test\n[x] rendered task\n[ ] open task\n"
                                                 "Colors #3B6FD8 #FFD25A **bold** `code`\n"
                                                 f"[img {pid}]", "markdown")
        note.color_identifier = "Yellow"
        self.store.save_note(note)
        win.open_folder(folder)
        self.show_panel()

        def finish():
            out = Path(self.selftest_dir)
            out.mkdir(parents=True, exist_ok=True)
            alloc = win.get_allocation()
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, alloc.width, alloc.height)
            cr = cairo.Context(surface)
            gradient = cairo.LinearGradient(0, 0, alloc.width, alloc.height)
            gradient.add_color_stop_rgb(0, .55, .72, .92)
            gradient.add_color_stop_rgb(1, .18, .38, .72)
            cr.set_source(gradient)
            cr.paint()
            cr.push_group()
            win.draw(cr)
            cr.pop_group_to_source()
            cr.paint()
            surface.write_to_png(str(out / "screenshot.png"))

            icon_theme = Gtk.IconTheme.get_default()
            icons = ["go-previous-symbolic", "system-search-symbolic", "list-add-symbolic",
                     "emblem-system-symbolic", "view-pin-symbolic", "folder-symbolic",
                     "edit-delete-symbolic", "x-office-calendar-symbolic"]
            formats = sorted(f.get_name() for f in GdkPixbuf.Pixbuf.get_formats())
            toggle = shortcuts.get_global("toggle")
            report = {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "gtk": f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
                "transparent_window": win.transparent,
                "missing_icons": [i for i in icons if not icon_theme.has_icon(i)],
                "pixbuf_formats": formats,
                "cards": len(win.cards),
                "autostart_after_first_run": autostart_enabled(),
                "global_toggle_after_first_run": toggle,
                "hotkey_backend": type(self.hotkeys).__name__ if self.hotkeys else None,
            }
            report["ok"] = bool(not report["missing_icons"] and "png" in formats and report["cards"])
            (out / "report.json").write_text(json.dumps(report, indent=2))
            # Leave the machine as it was.
            set_autostart(False, "")
            if toggle:
                shortcuts.set_global("toggle", "")
            self.quit_app()
            return False

        GLib.timeout_add(2500, finish)
        return False
