"""Single-instance hand-off for Windows and macOS.

On Linux GApplication does this over D-Bus. Elsewhere there is usually no session bus,
so the running instance listens on a loopback TCP port, written with a random token to
a file only the user can read. A second launch (`hashieh --toggle`, the Start menu, the
Dock) sends its arguments there and exits.
"""

import hmac
import json
import os
import secrets
import socket
from typing import Callable, List, Optional

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .paths import DATA_DIR  # noqa: E402

INSTANCE_FILE = DATA_DIR / "instance.json"


def forward(argv: List[str]) -> bool:
    """Send argv to a running instance. True if one accepted it."""
    try:
        info = json.loads(INSTANCE_FILE.read_text())
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=2) as conn:
            conn.sendall(json.dumps({"token": info["token"], "argv": argv}).encode() + b"\n")
            return conn.recv(8).startswith(b"ok")
    except (OSError, ValueError, KeyError):
        return False


class Server:
    def __init__(self, on_argv: Callable[[List[str]], None]):
        self._on_argv = on_argv
        self._token = secrets.token_hex(16)
        self._service = Gio.SocketService()
        address = Gio.InetSocketAddress.new_from_string("127.0.0.1", 0)
        _, effective = self._service.add_address(address, Gio.SocketType.STREAM,
                                                 Gio.SocketProtocol.TCP, None)
        self._service.connect("incoming", self._on_incoming)
        self._service.start()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = INSTANCE_FILE.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"port": effective.get_port(), "token": self._token, "pid": os.getpid()}, f)
        os.replace(tmp, INSTANCE_FILE)

    def _on_incoming(self, _service, connection, _source) -> bool:
        stream = Gio.DataInputStream.new(connection.get_input_stream())
        line, _ = stream.read_line_utf8(None)
        argv: Optional[List[str]] = None
        try:
            message = json.loads(line or "")
            if hmac.compare_digest(str(message.get("token", "")), self._token):
                argv = [str(a) for a in message.get("argv", [])]
        except ValueError:
            pass
        connection.get_output_stream().write_all(b"ok\n" if argv is not None else b"no\n", None)
        connection.close(None)
        if argv is not None:
            GLib.idle_add(lambda: self._on_argv(argv) and False)
        return True

    def close(self) -> None:
        self._service.stop()
        try:
            if json.loads(INSTANCE_FILE.read_text()).get("token") == self._token:
                INSTANCE_FILE.unlink()
        except (OSError, ValueError):
            pass


def parse_argv(argv: List[str]) -> dict:
    """The panel options as a dict shaped like GApplication's parsed options."""
    opts = {}
    it = iter(argv)
    for arg in it:
        if arg in ("--toggle", "-t"):
            opts["toggle"] = True
        elif arg in ("--show", "--hide", "--quit", "--new-note-from-clipboard"):
            opts[arg[2:]] = True
        elif arg in ("--new-note", "-n"):
            opts["new-note"] = next(it, "")
        elif arg.startswith("--new-note="):
            opts["new-note"] = arg.split("=", 1)[1]
    return opts
