"""System-wide hotkeys on Windows and macOS.

Linux uses GNOME custom keybindings instead (see shortcuts.py), which work even when
the app isn't running. Here the running app owns the hotkey:

* Windows: RegisterHotKey on a dedicated thread with its own message loop.
* macOS: Carbon RegisterEventHotKey, delivered through the application event target.
  Carbon hotkeys don't need the Accessibility permission.

Accelerators use GTK syntax ("<Control><Alt>n"); Super/Meta map to the Windows key and
to Command.
"""

import ctypes
import queue
import sys
import threading
from typing import Callable, Dict, Optional, Tuple

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

_SUPERISH = (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.MOD4_MASK)


def supported() -> bool:
    return sys.platform in ("win32", "darwin")


def create(callback: Callable[[str], None]):
    """A hotkey manager calling `callback(action)` on the GTK main loop, or None."""
    if sys.platform == "win32":
        return _WindowsHotkeys(callback)
    if sys.platform == "darwin":
        return _MacHotkeys(callback)
    return None


def _parse(accel: str) -> Optional[Tuple[int, int]]:
    if not accel:
        return None
    key, mods = Gtk.accelerator_parse(accel)
    if not key:
        return None
    return Gdk.keyval_to_lower(key), int(mods)


def _on_main_loop(callback, action):
    def run():
        callback(action)
        return False
    GLib.idle_add(run)


# Windows ----------------------------------------------------------------------------------------

_WM_HOTKEY = 0x0312
_WM_APP = 0x8000
_MOD_ALT, _MOD_CONTROL, _MOD_SHIFT, _MOD_WIN, _MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
_VK_NAMED = {
    "space": 0x20, "Return": 0x0D, "Tab": 0x09, "Escape": 0x1B, "BackSpace": 0x08,
    "Delete": 0x2E, "Insert": 0x2D, "Home": 0x24, "End": 0x23, "Page_Up": 0x21,
    "Page_Down": 0x22, "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
}


def _windows_key(accel: str) -> Optional[Tuple[int, int]]:
    parsed = _parse(accel)
    if not parsed:
        return None
    keyval, mods = parsed
    name = Gdk.keyval_name(keyval) or ""
    if len(name) == 1 and name.isalnum():
        vk = ord(name.upper())
    elif name[:1] == "F" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        vk = 0x6F + int(name[1:])
    elif name in _VK_NAMED:
        vk = _VK_NAMED[name]
    else:
        char = Gdk.keyval_to_unicode(keyval)
        if not char:
            return None
        vk_scan = ctypes.windll.user32.VkKeyScanW
        vk_scan.argtypes, vk_scan.restype = [ctypes.c_wchar], ctypes.c_short
        scan = vk_scan(chr(char))
        if scan == -1 or (scan & 0xFF) == 0xFF:
            return None
        vk = scan & 0xFF
    win_mods = 0
    if mods & Gdk.ModifierType.SHIFT_MASK:
        win_mods |= _MOD_SHIFT
    if mods & Gdk.ModifierType.CONTROL_MASK:
        win_mods |= _MOD_CONTROL
    if mods & Gdk.ModifierType.MOD1_MASK:
        win_mods |= _MOD_ALT
    if mods & _SUPERISH:
        win_mods |= _MOD_WIN
    return win_mods, vk


class _WindowsHotkeys:
    _TEST_ID = 0xBFFF

    def __init__(self, callback):
        from ctypes import wintypes
        self._wintypes = wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32")
        self._user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                             wintypes.UINT, wintypes.UINT]
        self._user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                              wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self._user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                                    wintypes.WPARAM, wintypes.LPARAM]
        self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._callback = callback
        self._requests = queue.Queue()
        self._ids: Dict[str, int] = {}
        self._actions: Dict[int, str] = {}
        self._next_id = 1
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hashieh-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def _run(self):
        msg = self._wintypes.MSG()
        self._tid = self._kernel32.GetCurrentThreadId()
        self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # creates the message queue
        self._ready.set()
        while self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == _WM_HOTKEY:
                action = self._actions.get(int(msg.wParam))
                if action:
                    _on_main_loop(self._callback, action)
            elif msg.message == _WM_APP:
                while not self._requests.empty():
                    fn, args, reply = self._requests.get()
                    reply.put(fn(*args))

    def _call(self, fn, *args):
        """Hotkeys belong to the thread that registered them, so run on the hotkey thread."""
        reply = queue.Queue()
        self._requests.put((fn, args, reply))
        self._user32.PostThreadMessageW(self._tid, _WM_APP, 0, 0)
        return reply.get(timeout=5)

    def register(self, action: str, accel: str) -> bool:
        self.unregister(action)
        key = _windows_key(accel)
        if not key:
            return False
        hotkey_id = self._next_id
        self._next_id += 1
        if not self._call(self._user32.RegisterHotKey, None, hotkey_id, key[0] | _MOD_NOREPEAT, key[1]):
            return False
        self._ids[action] = hotkey_id
        self._actions[hotkey_id] = action
        return True

    def unregister(self, action: str) -> None:
        hotkey_id = self._ids.pop(action, None)
        if hotkey_id:
            self._actions.pop(hotkey_id, None)
            self._call(self._user32.UnregisterHotKey, None, hotkey_id)

    def unregister_all(self) -> None:
        for action in list(self._ids):
            self.unregister(action)

    def can_register(self, accel: str) -> bool:
        key = _windows_key(accel)
        if not key:
            return False
        if not self._call(self._user32.RegisterHotKey, None, self._TEST_ID, key[0], key[1]):
            return False
        self._call(self._user32.UnregisterHotKey, None, self._TEST_ID)
        return True


# macOS ------------------------------------------------------------------------------------------

def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


_MAC_KEYCODES = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07,
    "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10,
    "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "equal": 0x18,
    "9": 0x19, "7": 0x1A, "minus": 0x1B, "8": 0x1C, "0": 0x1D, "bracketright": 0x1E, "o": 0x1F,
    "u": 0x20, "bracketleft": 0x21, "i": 0x22, "p": 0x23, "Return": 0x24, "l": 0x25, "j": 0x26,
    "apostrophe": 0x27, "k": 0x28, "semicolon": 0x29, "backslash": 0x2A, "comma": 0x2B,
    "slash": 0x2C, "n": 0x2D, "m": 0x2E, "period": 0x2F, "Tab": 0x30, "space": 0x31, "grave": 0x32,
    "BackSpace": 0x33, "Escape": 0x35, "F1": 0x7A, "F2": 0x78, "F3": 0x63, "F4": 0x76, "F5": 0x60,
    "F6": 0x61, "F7": 0x62, "F8": 0x64, "F9": 0x65, "F10": 0x6D, "F11": 0x67, "F12": 0x6F,
    "Left": 0x7B, "Right": 0x7C, "Down": 0x7D, "Up": 0x7E,
}
_CMD, _SHIFT, _OPTION, _CONTROL = 0x0100, 0x0200, 0x0800, 0x1000


def _mac_key(accel: str) -> Optional[Tuple[int, int]]:
    parsed = _parse(accel)
    if not parsed:
        return None
    keyval, mods = parsed
    code = _MAC_KEYCODES.get(Gdk.keyval_name(keyval) or "")
    if code is None:
        return None
    mac_mods = 0
    if mods & (_SUPERISH | Gdk.ModifierType.MOD2_MASK):
        mac_mods |= _CMD
    if mods & Gdk.ModifierType.SHIFT_MASK:
        mac_mods |= _SHIFT
    if mods & Gdk.ModifierType.MOD1_MASK:
        mac_mods |= _OPTION
    if mods & Gdk.ModifierType.CONTROL_MASK:
        mac_mods |= _CONTROL
    return mac_mods, code


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class _MacHotkeys:
    _SIGNATURE = _fourcc("HSHH")
    _TEST_ID = 0xBFFF

    def __init__(self, callback):
        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, _HANDLER, ctypes.c_ulong,
                                               ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_void_p)]
        carbon.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                             ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                                             ctypes.c_void_p]
        carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID,
                                               ctypes.c_void_p, ctypes.c_uint32,
                                               ctypes.POINTER(ctypes.c_void_p)]
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        self._carbon = carbon
        self._callback = callback
        self._refs: Dict[str, ctypes.c_void_p] = {}
        self._ids: Dict[str, int] = {}
        self._actions: Dict[int, str] = {}
        self._next_id = 1
        self._target = carbon.GetApplicationEventTarget()
        spec = _EventTypeSpec(_fourcc("keyb"), 5)  # kEventClassKeyboard, kEventHotKeyPressed
        self._handler = _HANDLER(self._on_event)    # keep a reference: Carbon holds a raw pointer
        self._handler_ref = ctypes.c_void_p()
        carbon.InstallEventHandler(self._target, self._handler, 1, ctypes.byref(spec), None,
                                   ctypes.byref(self._handler_ref))

    def _on_event(self, _call_ref, event, _user_data):
        hotkey = _EventHotKeyID()
        self._carbon.GetEventParameter(event, _fourcc("----"), _fourcc("hkid"), None,
                                       ctypes.sizeof(hotkey), None, ctypes.byref(hotkey))
        action = self._actions.get(hotkey.id)
        if action:
            _on_main_loop(self._callback, action)
        return 0

    def _register(self, key, hotkey_id) -> Optional[ctypes.c_void_p]:
        ref = ctypes.c_void_p()
        status = self._carbon.RegisterEventHotKey(key[1], key[0], _EventHotKeyID(self._SIGNATURE, hotkey_id),
                                                  self._target, 0, ctypes.byref(ref))
        return ref if status == 0 else None

    def register(self, action: str, accel: str) -> bool:
        self.unregister(action)
        key = _mac_key(accel)
        if not key:
            return False
        hotkey_id = self._next_id
        self._next_id += 1
        ref = self._register(key, hotkey_id)
        if ref is None:
            return False
        self._refs[action] = ref
        self._ids[action] = hotkey_id
        self._actions[hotkey_id] = action
        return True

    def unregister(self, action: str) -> None:
        ref = self._refs.pop(action, None)
        if ref is not None:
            self._carbon.UnregisterEventHotKey(ref)
            self._actions.pop(self._ids.pop(action, 0), None)

    def unregister_all(self) -> None:
        for action in list(self._refs):
            self.unregister(action)

    def can_register(self, accel: str) -> bool:
        key = _mac_key(accel)
        ref = self._register(key, self._TEST_ID) if key else None
        if ref is None:
            return False
        self._carbon.UnregisterEventHotKey(ref)
        return True
