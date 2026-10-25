from __future__ import annotations

from harness_lab_app import docs_catalog


def test_list_docs_exposes_expected_fields():
    docs = docs_catalog.list_docs()
    assert docs, "expected a non-empty curated doc index"
    for entry in docs:
        assert set(entry) == {"slug", "title", "description", "group", "file"}
    # The key operator docs are surfaced.
    slugs = {d["slug"] for d in docs}
    assert {"web-ui", "claude-codex", "improvement-cycle"} <= slugs


def test_read_doc_returns_content_for_known_slug():
    doc = docs_catalog.read_doc("improvement-cycle")
    assert doc is not None
    assert doc["slug"] == "improvement-cycle"
    assert doc["title"]
    assert len(doc["content"]) > 0


def test_the_start_page_is_served_first_then_the_six_journeys():
    """A new developer meets what the lab is for, then the journeys in order."""
    slugs = [d["slug"] for d in docs_catalog.list_docs()]
    assert slugs[:7] == [
        "start",
        "create-case",
        "run",
        "read-results",
        "bring-your-own",
        "take-it-home",
        "improve-template",
    ]
    doc = docs_catalog.read_doc("start")
    assert doc is not None
    assert "make harness-check" in doc["content"]


def test_every_catalogued_doc_exists_on_disk():
    """read_doc answers even for a missing file, so check the file itself."""
    from harness_lab_app.config import get_settings

    for entry in docs_catalog._DOCS:
        path = get_settings().repo_root / "docs" / entry["file"]
        assert path.is_file(), entry["file"]


def test_the_demo_guide_is_still_served():
    doc = docs_catalog.read_doc("demo")
    assert doc is not None
    assert "make harness-check" in doc["content"]


def test_read_doc_rejects_unknown_or_traversal_slug():
    assert docs_catalog.read_doc("does-not-exist") is None
    # Slug is whitelisted, so a traversal attempt resolves to nothing.
    assert docs_catalog.read_doc("../config") is None


def test_each_entry_names_its_file_so_links_between_docs_resolve():
    """docs/README.md links `journey-create-a-case.md`; the Guide maps that file
    name to its slug to open it in place."""
    files = {d["file"]: d["slug"] for d in docs_catalog.list_docs()}
    assert files["journey-create-a-case.md"] == "create-case"
    assert files["README.md"] == "start"
