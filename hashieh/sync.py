"""Sync notes through the user's Google Drive.

Data goes to the Drive *app data folder*: a hidden folder only Hashieh can see,
which does not count against anything the user browses. It holds

* `hashieh-sync.json`: every folder and note, plus deletions, as
  {"version", "folders": [...], "notes": [...], "deleted": {id: {"kind", "at"}}};
* `picture-<ID>`: one file per picture. Pictures never change once added.

Each device merges its notes with that file, row by row: the copy changed last
wins, and a deletion wins over a copy changed before it. Clocks are trusted.

Signing in uses OAuth 2.0 for installed apps: the browser goes to Google and
comes back to a one-off server on 127.0.0.1, with PKCE. Only the refresh token
is kept, in the settings folder.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .model import Store
from .paths import CONFIG_DIR, IS_WINDOWS

# OAuth client of type "Desktop app" from the Google Cloud console, with the Drive API
# enabled. Google treats a desktop client's secret as public, so it ships in the app.
CLIENT_ID = os.environ.get("HASHIEH_GOOGLE_CLIENT_ID", "255350379631-f39vjn0o4uve2rni2vfi1bkmjh5lt6q5.apps.googleusercontent.com")
CLIENT_SECRET = os.environ.get("HASHIEH_GOOGLE_CLIENT_SECRET", "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")
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.appdata"
SCOPES = "openid email " + DRIVE_SCOPE

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"

ACCOUNT_PATH = CONFIG_DIR / "google-account.json"
SYNC_FILE = "hashieh-sync.json"
PICTURE_PREFIX = "picture-"
FORMAT_VERSION = 1
KEEP_DELETIONS = 180 * 86400  # a device offline for longer may bring deleted notes back
SIGN_IN_TIMEOUT = 300


class SyncError(Exception):
    """Sync could not finish; the message is meant for the user."""


class AuthError(SyncError):
    """The account no longer works (access revoked, password changed): sign in again."""


NO_DRIVE_ACCESS = ("Hashieh wasn't allowed to use Google Drive. Sign in again and, on Google's "
                   "page, tick the box that lets Hashieh see its own data in your Google Drive.")


def _google_message(body: str) -> str:
    try:
        return json.loads(body)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return ""


def configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def _ssl_context() -> ssl.SSLContext:
    # The Windows and macOS builds carry no system CA paths of their own; use certifi's.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL = _ssl_context()


def _http(method: str, url: str, data: Optional[bytes] = None, headers: Optional[dict] = None,
          timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL) as response:
        return response.read()


def _post_form(url: str, fields: dict) -> dict:
    try:
        body = _http("POST", url, urllib.parse.urlencode(fields).encode(),
                     {"Content-Type": "application/x-www-form-urlencoded"})
    except urllib.error.HTTPError as e:
        try:
            error = json.loads(e.read()).get("error", "")
        except ValueError:
            error = ""
        if error == "invalid_grant":
            raise AuthError("Google access was revoked or expired. Sign in again.") from e
        raise SyncError(f"Google sign-in failed ({e.code} {error or e.reason}).") from e
    except (urllib.error.URLError, OSError) as e:
        raise SyncError(f"Can't reach Google: {getattr(e, 'reason', e)}") from e
    return json.loads(body)


# Account ----------------------------------------------------------------------------------------

class Account:
    def __init__(self, email: str, refresh_token: str):
        self.email = email
        self.refresh_token = refresh_token
        self._access_token = ""
        self._expires = 0.0

    @classmethod
    def load(cls) -> Optional["Account"]:
        try:
            data = json.loads(ACCOUNT_PATH.read_text())
            return cls(data["email"], data["refresh_token"])
        except (OSError, ValueError, KeyError):
            return None

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ACCOUNT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"email": self.email, "refresh_token": self.refresh_token}))
        if not IS_WINDOWS:
            tmp.chmod(0o600)
        tmp.replace(ACCOUNT_PATH)

    def access_token(self) -> str:
        if time.time() > self._expires - 60:
            data = _post_form(TOKEN_URL, {
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
            self._access_token = data["access_token"]
            self._expires = time.time() + float(data.get("expires_in", 3600))
        return self._access_token

    @staticmethod
    def forget() -> None:
        ACCOUNT_PATH.unlink(missing_ok=True)

    def revoke(self) -> None:
        """Ask Google to end this app's access (best effort; call forget() first)."""
        try:
            _post_form(REVOKE_URL, {"token": self.refresh_token})
        except SyncError:
            pass


def _id_token_email(id_token: str) -> str:
    # The token comes straight from Google's token endpoint over TLS, so it isn't verified again.
    payload = id_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email", "")


_DONE_PAGE = b"""<!doctype html><meta charset="utf-8"><title>Hashieh</title>
<body style="font:16px sans-serif;text-align:center;margin-top:20vh">
<h2>%s</h2><p>You can close this tab and go back to Hashieh.</p>"""


def sign_in(open_url: Callable[[str], None], timeout: float = SIGN_IN_TIMEOUT) -> Account:
    """Let the user sign in in their browser; returns the account, saved."""
    if not configured():
        raise SyncError("Google sync isn't set up in this build of Hashieh.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state") != [state]:
                self.send_error(404)
                return
            result.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_DONE_PAGE % (b"Signed in" if "code" in result else b"Sign-in cancelled"))

        def log_message(self, *_):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    redirect = f"http://127.0.0.1:{server.server_port}"
    try:
        open_url(AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": CLIENT_ID, "redirect_uri": redirect, "response_type": "code",
            "scope": SCOPES, "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"}))
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "code" not in result:
        raise SyncError("Sign-in was cancelled." if result else "Sign-in timed out.")
    data = _post_form(TOKEN_URL, {
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "code": result["code"],
        "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"})
    if "refresh_token" not in data:
        raise SyncError("Google didn't grant offline access. Try signing in again.")
    if DRIVE_SCOPE not in data.get("scope", DRIVE_SCOPE).split():
        Account("", data["refresh_token"]).revoke()
        raise SyncError(NO_DRIVE_ACCESS)
    account = Account(_id_token_email(data.get("id_token", "")) or "Google account",
                      data["refresh_token"])
    account._access_token = data["access_token"]
    account._expires = time.time() + float(data.get("expires_in", 3600))
    account.save()
    return account


# Drive ------------------------------------------------------------------------------------------

class Drive:
    """The few Drive v3 calls sync needs, all inside the app data folder."""

    def __init__(self, account: Account):
        self.account = account

    def _call(self, method: str, url: str, data: Optional[bytes] = None,
              headers: Optional[dict] = None) -> bytes:
        for attempt in (1, 2):
            auth = {"Authorization": f"Bearer {self.account.access_token()}"}
            try:
                return _http(method, url, data, {**auth, **(headers or {})})
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 1:
                    self.account._expires = 0  # token expired early: refresh and retry once
                    continue
                detail = e.read().decode(errors="replace")
                if "accessNotConfigured" in detail or "SERVICE_DISABLED" in detail:
                    raise SyncError("The Google Drive API isn't enabled for this app.") from e
                if "insufficientPermissions" in detail or "SCOPE_INSUFFICIENT" in detail:
                    raise AuthError(NO_DRIVE_ACCESS) from e
                if e.code == 401:
                    raise AuthError("Google access was revoked or expired. Sign in again.") from e
                raise SyncError(f"Google Drive error {e.code}: "
                                f"{_google_message(detail) or e.reason}") from e
            except (urllib.error.URLError, OSError) as e:
                raise SyncError(f"Can't reach Google Drive: {getattr(e, 'reason', e)}") from e
        raise AssertionError("unreachable")

    def files(self) -> list:
        """[{"id", "name", "headRevisionId"}] of the app data folder, oldest first."""
        files, page = [], ""
        while True:
            query = {"spaces": "appDataFolder", "pageSize": "1000", "orderBy": "createdTime",
                     "fields": "nextPageToken,files(id,name,headRevisionId)"}
            if page:
                query["pageToken"] = page
            data = json.loads(self._call("GET", FILES_URL + "?" + urllib.parse.urlencode(query)))
            files += data.get("files", [])
            page = data.get("nextPageToken")
            if not page:
                return files

    def revision(self, file_id: str) -> str:
        data = json.loads(self._call("GET", f"{FILES_URL}/{file_id}?fields=headRevisionId"))
        return data.get("headRevisionId") or ""

    def download(self, file_id: str) -> bytes:
        return self._call("GET", f"{FILES_URL}/{file_id}?alt=media")

    def create(self, name: str, data: bytes) -> None:
        boundary = secrets.token_hex(16)
        meta = json.dumps({"name": name, "parents": ["appDataFolder"]}).encode()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                + meta + f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
                + data + f"\r\n--{boundary}--\r\n".encode())
        self._call("POST", f"{UPLOAD_URL}?uploadType=multipart&fields=id", body,
                   {"Content-Type": f"multipart/related; boundary={boundary}"})

    def update(self, file_id: str, data: bytes) -> None:
        self._call("PATCH", f"{UPLOAD_URL}/{file_id}?uploadType=media&fields=id", data,
                   {"Content-Type": "application/octet-stream"})

    def delete(self, file_id: str) -> None:
        self._call("DELETE", f"{FILES_URL}/{file_id}")


# Merge ------------------------------------------------------------------------------------------

def _newer(a: dict, b: dict) -> bool:
    # Ties go to the same copy on every device, so two devices can't keep swapping them.
    return (a["modified"], json.dumps(a, sort_keys=True)) > (b["modified"], json.dumps(b, sort_keys=True))


def merge(local: dict, remote: dict, now: Optional[float] = None) -> dict:
    """Merge two sets of sync rows (see Store.sync_rows) into one."""
    now = time.time() if now is None else now
    deleted = {}
    for side in (local, remote):
        for row_id, d in side["deleted"].items():
            if row_id not in deleted or d["at"] > deleted[row_id]["at"]:
                deleted[row_id] = d
    merged = {"deleted": deleted}
    for table in ("folders", "notes"):
        rows = {}
        for side in (local, remote):
            for row_id, row in side[table].items():
                if row_id not in rows or _newer(row, rows[row_id]):
                    rows[row_id] = row
        for row_id in list(rows):
            if row_id in deleted:
                if deleted[row_id]["at"] >= rows[row_id]["modified"]:
                    del rows[row_id]
                else:  # changed after it was deleted elsewhere: keep it
                    del deleted[row_id]
        merged[table] = rows
    # Notes go with their folder, as they do when a folder is deleted here.
    for row_id, note in list(merged["notes"].items()):
        if note["folder_id"] is not None and note["folder_id"] not in merged["folders"]:
            del merged["notes"][row_id]
            deleted[row_id] = {"kind": "note", "at": now}
    for row_id in [i for i, d in deleted.items() if d["at"] < now - KEEP_DELETIONS]:
        del deleted[row_id]
    return merged


def _encode(rows: dict) -> bytes:
    return json.dumps({"version": FORMAT_VERSION,
                       "folders": list(rows["folders"].values()),
                       "notes": list(rows["notes"].values()),
                       "deleted": rows["deleted"]}, ensure_ascii=False).encode()


def _decode(data: bytes) -> dict:
    doc = json.loads(data)
    if doc.get("version", 0) > FORMAT_VERSION:
        raise SyncError("Your notes were synced by a newer Hashieh. Update Hashieh to keep syncing.")
    return {"folders": {r["id"]: r for r in doc.get("folders", [])},
            "notes": {r["id"]: r for r in doc.get("notes", [])},
            "deleted": doc.get("deleted", {})}


EMPTY = {"folders": {}, "notes": {}, "deleted": {}}


class Result:
    def __init__(self):
        self.received = self.pictures_received = self.pictures_sent = 0
        self.sent = False

    def __str__(self):
        parts = []
        if self.received:
            parts.append(f"{self.received} change{'s' if self.received != 1 else ''} received")
        if self.sent:
            parts.append("changes sent")
        if self.pictures_received or self.pictures_sent:
            parts.append(f"{self.pictures_received + self.pictures_sent} picture(s)")
        return ", ".join(parts) or "up to date"


def sync(store: Store, drive: Drive) -> Result:
    """One full sync of `store` with Drive."""
    result = Result()
    for _ in range(3):
        files = drive.files()
        # Two devices syncing for the first time at once can both create the file:
        # merge every copy, keep the oldest and remove the rest.
        copies = [f for f in files if f["name"] == SYNC_FILE]
        meta = copies[0] if copies else None
        remote = EMPTY
        try:
            for f in copies:
                remote = merge(remote, _decode(drive.download(f["id"])))
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise SyncError("The synced notes on Google Drive are damaged.") from e
        base = store.sync_rows()
        merged = merge(base, remote)
        result.received += store.apply_sync(base, merged)

        local_pictures = set(store.picture_ids())
        remote_pictures = {f["name"][len(PICTURE_PREFIX):]: f["id"] for f in files
                           if f["name"].startswith(PICTURE_PREFIX)}
        for pid in local_pictures - remote_pictures.keys():
            drive.create(PICTURE_PREFIX + pid, store.picture(pid))
            result.pictures_sent += 1
        for pid in remote_pictures.keys() - local_pictures:
            store.add_picture(drive.download(remote_pictures[pid]), pid)
            result.pictures_received += 1

        if merged == remote and len(copies) <= 1:
            return result
        data = _encode(merged)
        if meta is None:
            drive.create(SYNC_FILE, data)
        elif drive.revision(meta["id"]) == (meta.get("headRevisionId") or ""):
            drive.update(meta["id"], data)
        else:
            continue  # another device synced meanwhile: merge with its copy too
        for f in copies[1:]:
            drive.delete(f["id"])
        result.sent = merged != remote
        return result
    raise SyncError("Another device keeps syncing at the same time. Try again in a moment.")
