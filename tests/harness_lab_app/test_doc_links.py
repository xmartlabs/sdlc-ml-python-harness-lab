"""The entry docs and the journeys never link to a page or a heading that is gone.

A developer meets the lab through README.md, docs/README.md, the onboarding and
the six journeys (ADR-0072). A dead link there is the first thing they hit, and
nothing else checks it: the docs gate only asks that docs change with code.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRY_DOCS = [
    ROOT / "README.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "onboarding.md",
    *sorted((ROOT / "docs").glob("journey-*.md")),
]
_LINK = re.compile(r"\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^#+ (.+)$", re.MULTILINE)


def _anchor(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, punctuation dropped, spaces to dashes."""
    return re.sub(r"[^a-z0-9 _-]", "", heading.lower()).replace(" ", "-")


def _local_links(doc: Path) -> list[str]:
    return [
        target
        for target in _LINK.findall(doc.read_text())
        if not target.startswith(("http://", "https://", "mailto:"))
    ]


@pytest.mark.parametrize("doc", ENTRY_DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_link_in_an_entry_doc_resolves(doc):
    broken = []
    for target in _local_links(doc):
        path, _, fragment = target.partition("#")
        dest = (doc.parent / path).resolve() if path else doc
        if not dest.exists():
            broken.append(f"{target}: no such file")
            continue
        if fragment and dest.suffix == ".md":
            anchors = {_anchor(h) for h in _HEADING.findall(dest.read_text())}
            if fragment not in anchors:
                broken.append(f"{target}: no such heading")
    assert broken == [], broken


def test_the_six_journeys_exist_and_each_links_to_the_next():
    order = [
        "journey-create-a-case.md",
        "journey-run-and-compare.md",
        "journey-read-results.md",
        "journey-bring-your-repo-or-skill.md",
        "journey-take-it-home.md",
        "journey-improve-the-template.md",
    ]
    for current, following in itertools.pairwise(order):
        text = (ROOT / "docs" / current).read_text()
        assert f"]({following})" in text, f"{current} does not lead to {following}"
