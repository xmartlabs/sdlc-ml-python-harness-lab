"""Functional tests for the notes service. Run from the workspace root:

python -m pytest -q tests
"""

from __future__ import annotations

import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notes_service import AuthError, NotesService, NotFoundError


@pytest.fixture
def service(tmp_path: Path) -> NotesService:
    return NotesService(tmp_path / "data")


@pytest.fixture
def alice(service: NotesService) -> str:
    service.register("alice", "correct horse")
    return service.login("alice", "correct horse")


def test_login_rejects_a_wrong_password(service: NotesService) -> None:
    service.register("bob", "s3cret")
    with pytest.raises(AuthError):
        service.login("bob", "nope")


def test_notes_round_trip(service: NotesService, alice: str) -> None:
    note_id = service.create_note(alice, "Groceries", "milk, eggs")
    assert service.get_note(alice, note_id)["body"] == "milk, eggs"
    service.update_note(alice, note_id, "Groceries", "milk, eggs, bread")
    assert service.get_note(alice, note_id)["body"] == "milk, eggs, bread"
    service.delete_note(alice, note_id)
    with pytest.raises(NotFoundError):
        service.get_note(alice, note_id)


def test_search_matches_title_and_body(service: NotesService, alice: str) -> None:
    service.create_note(alice, "Trip to Lisbon", "book flights")
    service.create_note(alice, "Work", "quarterly report for Lisbon office")
    service.create_note(alice, "Misc", "nothing here")
    titles = [note["title"] for note in service.search_notes(alice, "lisbon")]
    assert titles == ["Trip to Lisbon", "Work"]


def test_attachments_round_trip(service: NotesService, alice: str) -> None:
    note_id = service.create_note(alice, "Receipts", "")
    service.save_attachment(alice, note_id, "march.pdf", b"%PDF-1.4 ...")
    assert service.list_attachments(alice, note_id) == ["march.pdf"]
    assert service.read_attachment(alice, note_id, "march.pdf") == b"%PDF-1.4 ..."


def test_export_writes_an_archive(service: NotesService, alice: str) -> None:
    service.create_note(alice, "One", "first")
    archive = service.export_notes(alice, "Q3 notes (draft).tgz")
    with tarfile.open(archive) as tar:
        names = sorted(
            Path(name).name
            for name in tar.getnames()
            if name.endswith(".txt") and not Path(name).name.startswith("._")
        )
    assert names == ["1.txt"]


def test_preferences_move_between_accounts(service: NotesService, alice: str) -> None:
    service.set_preferences(alice, {"theme": "dark", "page_size": 50})
    blob = service.export_preferences(alice)
    service.register("carol", "pw")
    carol = service.login("carol", "pw")
    service.import_preferences(carol, blob)
    assert service.get_preferences(carol) == {"theme": "dark", "page_size": 50}
