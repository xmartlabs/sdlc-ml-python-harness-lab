"""Harness lifecycle domain: versioned proposals and (later) release adoption.

Phase 3 ships the proposal layer: Pydantic contracts (:mod:`.models`), atomic YAML
persistence (:mod:`.manifests`), ID allocation and evidence extraction
(:mod:`.proposals`), and public-output sanitization (:mod:`.sanitizer`). The CLI
(:mod:`.cli`) wires these to run evidence and GitHub issue creation.
"""

from __future__ import annotations
