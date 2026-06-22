---
name: refactor_to_clean_architecture
description: Use when refactoring modules to align dependency direction, responsibilities, and boundaries with clean architecture.
---

# Skill: refactor_to_clean_architecture

## Purpose

Refactor existing modules to align with clean architecture boundaries.

## Required Input

- Current module(s) and pain points.
- Target boundary violations to fix.
- Non-functional constraints (time/risk/backward compatibility).

## Output Format

- Current vs target structure summary.
- Ordered refactor plan.
- File-level changes with rationale.
- Risk list and rollback notes.

## Execution Rules

1. Fix boundary leaks before cosmetic changes.
2. Preserve behavior unless explicitly requested otherwise.
3. Keep refactor scope minimal and test-supported.
