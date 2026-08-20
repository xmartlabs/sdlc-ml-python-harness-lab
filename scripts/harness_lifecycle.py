#!/usr/bin/env python3
"""Thin CLI wrapper for the harness lifecycle domain.

Business logic lives in ``harness_lab_app.harness_lifecycle``; this script only
delegates so ``make`` targets and operators have a stable entry point.
"""

from __future__ import annotations

from harness_lab_app.harness_lifecycle.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
