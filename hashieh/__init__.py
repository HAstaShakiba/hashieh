"""Hashieh: a side-panel notes app for Linux that reads and writes SideNotes (macOS) data."""

import os

__version__ = "0.1.0"
APP_NAME = "Hashieh"
# Overridable so a second, isolated instance (e.g. for testing) does not talk to the running one.
APP_ID = os.environ.get("HASHIEH_APP_ID", "io.github.hastashakiba.Hashieh")
