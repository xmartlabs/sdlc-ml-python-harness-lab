"""The notes service's public API.

Every call after ``login`` takes the session token it returned. A note belongs to
the user who created it.

Data layout under ``data_dir``::

    notes.db                      accounts, sessions, notes, preferences
    attachments/<note_id>/<name>  files attached to a note
    exports/<archive_name>        gzip'd tar archives made by ``export_notes``
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pickle
import secrets
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from notes_service.db import connect

DEFAULT_PREFERENCES: dict[str, Any] = {"theme": "light", "page_size": 20}
_HASH_ITERATIONS = 10_000


class AuthError(Exception):
    """Unknown user, wrong password or invalid session token."""


class NotFoundError(Exception):
    """The requested note or attachment does not exist."""


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _HASH_ITERATIONS
    )
    return digest.hex()


class NotesService:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir = self.data_dir / "attachments"
        self.exports_dir = self.data_dir / "exports"
        self.attachments_dir.mkdir(exist_ok=True)
        self.exports_dir.mkdir(exist_ok=True)
        self.db = connect(self.data_dir / "notes.db")

    # --- accounts --------------------------------------------------------

    def register(self, username: str, password: str) -> int:
        """Create an account and return its id. Usernames are unique."""
        if not username or not password:
            raise ValueError("username and password are required")
        salt = secrets.token_hex(16)
        try:
            cursor = self.db.execute(
                "INSERT INTO users (username, password_hash, salt, preferences) "
                "VALUES (?, ?, ?, ?)",
                (
                    username,
                    _hash_password(password, salt),
                    salt,
                    json.dumps(DEFAULT_PREFERENCES),
                ),
            )
        except sqlite3.IntegrityError as error:
            self.db.rollback()
            raise ValueError(f"username {username!r} is taken") from error
        self.db.commit()
        return int(cursor.lastrowid)

    def login(self, username: str, password: str) -> str:
        """Return a new session token for valid credentials."""
        row = self.db.execute(
            "SELECT id, password_hash, salt FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None or not hmac.compare_digest(
            row["password_hash"], _hash_password(password, row["salt"])
        ):
            raise AuthError("invalid credentials")
        token = secrets.token_hex(24)
        self.db.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, row["id"])
        )
        self.db.commit()
        return token

    def _user_id(self, token: str) -> int:
        row = self.db.execute(
            "SELECT user_id FROM sessions WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            raise AuthError("invalid session")
        return int(row["user_id"])

    # --- notes -----------------------------------------------------------

    def create_note(self, token: str, title: str, body: str) -> int:
        user_id = self._user_id(token)
        cursor = self.db.execute(
            "INSERT INTO notes (owner_id, title, body) VALUES (?, ?, ?)",
            (user_id, title, body),
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def get_note(self, token: str, note_id: int) -> dict[str, Any]:
        self._user_id(token)
        row = self.db.execute(
            "SELECT id, owner_id, title, body FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"note {note_id}")
        return dict(row)

    def list_notes(self, token: str) -> list[dict[str, Any]]:
        """The caller's notes, oldest first."""
        user_id = self._user_id(token)
        rows = self.db.execute(
            "SELECT id, owner_id, title, body FROM notes WHERE owner_id = ? "
            "ORDER BY id",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_note(self, token: str, note_id: int, title: str, body: str) -> None:
        self._user_id(token)
        cursor = self.db.execute(
            "UPDATE notes SET title = ?, body = ? WHERE id = ?",
            (title, body, note_id),
        )
        self.db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError(f"note {note_id}")

    def delete_note(self, token: str, note_id: int) -> None:
        self._user_id(token)
        cursor = self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError(f"note {note_id}")
        shutil.rmtree(self.attachments_dir / str(note_id), ignore_errors=True)

    def search_notes(self, token: str, query: str) -> list[dict[str, Any]]:
        """The caller's notes whose title or body contains ``query``.

        Case-insensitive substring match, oldest first.
        """
        user_id = self._user_id(token)
        sql = (
            "SELECT id, owner_id, title, body FROM notes "
            f"WHERE owner_id = {user_id} "
            f"AND (title LIKE '%{query}%' OR body LIKE '%{query}%') ORDER BY id"
        )
        return [dict(row) for row in self.db.execute(sql).fetchall()]

    # --- attachments -----------------------------------------------------

    def _attachment_path(self, note_id: int, filename: str) -> str:
        return os.path.join(self.attachments_dir, str(note_id), filename)

    def save_attachment(
        self, token: str, note_id: int, filename: str, content: bytes
    ) -> None:
        self._user_id(token)
        if (
            self.db.execute("SELECT 1 FROM notes WHERE id = ?", (note_id,)).fetchone()
            is None
        ):
            raise NotFoundError(f"note {note_id}")
        path = self._attachment_path(note_id, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)

    def read_attachment(self, token: str, note_id: int, filename: str) -> bytes:
        self._user_id(token)
        path = self._attachment_path(note_id, filename)
        if not os.path.isfile(path):
            raise NotFoundError(f"attachment {filename!r} of note {note_id}")
        with open(path, "rb") as handle:
            return handle.read()

    def list_attachments(self, token: str, note_id: int) -> list[str]:
        """File names attached to the note, sorted."""
        self._user_id(token)
        folder = self.attachments_dir / str(note_id)
        if not folder.is_dir():
            return []
        return sorted(entry.name for entry in folder.iterdir() if entry.is_file())

    # --- exports ---------------------------------------------------------

    def export_notes(self, token: str, archive_name: str) -> Path:
        """Write the caller's notes to ``exports/<archive_name>`` as a .tar.gz.

        Archive names are user-chosen labels such as ``Q3 notes (draft).tgz``.
        Each note becomes ``<id>.txt`` holding its title, a blank line and its
        body. Returns the archive's path.
        """
        notes = self.list_notes(token)
        staging = self.exports_dir / f".staging-{secrets.token_hex(8)}"
        staging.mkdir()
        try:
            for note in notes:
                (staging / f"{note['id']}.txt").write_text(
                    f"{note['title']}\n\n{note['body']}\n"
                )
            archive = os.path.join(self.exports_dir, archive_name)
            subprocess.run(
                f'tar -czf "{archive}" -C "{staging}" .', shell=True, check=True
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return Path(archive)

    # --- preferences -----------------------------------------------------

    def get_preferences(self, token: str) -> dict[str, Any]:
        user_id = self._user_id(token)
        row = self.db.execute(
            "SELECT preferences FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(json.loads(row["preferences"]))

    def set_preferences(self, token: str, preferences: dict[str, Any]) -> None:
        user_id = self._user_id(token)
        if not isinstance(preferences, dict):
            raise ValueError("preferences must be a mapping")
        self.db.execute(
            "UPDATE users SET preferences = ? WHERE id = ?",
            (json.dumps(preferences), user_id),
        )
        self.db.commit()

    def export_preferences(self, token: str) -> bytes:
        """A portable blob a user can import on another account or instance."""
        return pickle.dumps(self.get_preferences(token))

    def import_preferences(self, token: str, blob: bytes) -> None:
        """Replace the caller's preferences with an exported blob."""
        preferences = pickle.loads(blob)
        self.set_preferences(token, preferences)
