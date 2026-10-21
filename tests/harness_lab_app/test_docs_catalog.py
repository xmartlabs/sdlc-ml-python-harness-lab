from __future__ import annotations

from harness_lab_app import docs_catalog


def test_list_docs_exposes_expected_fields():
    docs = docs_catalog.list_docs()
    assert docs, "expected a non-empty curated doc index"
    for entry in docs:
        assert set(entry) == {"slug", "title", "description", "group"}
    # The key operator docs are surfaced.
    slugs = {d["slug"] for d in docs}
    assert {"web-ui", "claude-codex", "improvement-cycle"} <= slugs


def test_read_doc_returns_content_for_known_slug():
    doc = docs_catalog.read_doc("improvement-cycle")
    assert doc is not None
    assert doc["slug"] == "improvement-cycle"
    assert doc["title"]
    assert len(doc["content"]) > 0


def test_the_demo_guide_is_served_first():
    """Home links to it; a new user should meet it before anything else."""
    docs = docs_catalog.list_docs()
    assert docs[0]["slug"] == "demo"
    doc = docs_catalog.read_doc("demo")
    assert doc is not None
    assert "make harness-check" in doc["content"]


def test_every_catalogued_doc_exists():
    for entry in docs_catalog.list_docs():
        assert docs_catalog.read_doc(entry["slug"]) is not None, entry["slug"]


def test_read_doc_rejects_unknown_or_traversal_slug():
    assert docs_catalog.read_doc("does-not-exist") is None
    # Slug is whitelisted, so a traversal attempt resolves to nothing.
    assert docs_catalog.read_doc("../config") is None
