# ADR-0100: Fixed games run as baked builds in a slim runtime

- **Status:** Accepted
- **Date:** 2026-11-24
- **Scope:** Fixed racing gameplay; extends ADR-0098
- **Guide:** [Tidal Ring gameplay](../racing-gameplay.md)

## Context

The lab is Docker-first. Fixed games need portable executable builds, numerical
headless control and graphical play without inheriting the coding-agent image.
The first racing game already uses deterministic JavaScript rules and Three.js.
Installing another engine or a desktop renderer would duplicate those rules and
increase the runtime's dependencies.

## Decision

Build the standalone React/Three.js game with locked npm dependencies in a pinned
Node builder. A separate pinned Alpine runtime contains only the Node executable,
its required shared libraries, the shared rules, a dependency-free HTTP/NDJSON
entry point and the compiled browser assets. No source mount covers these files.
The browser supplies WebGL; headless sessions need no renderer, GPU or model weights.

Compose runs one bounded, non-root, read-only game-runtime service. API and Celery
use its private HTTP sessions for human input re-simulation and model episodes.
The lab proxies only `/game/` assets; the internal session API is not published.
Python keeps session authentication, model credentials, database records and PR1
recordings. A host Node subprocess remains an explicit development/test fallback.

Persist the engine SHA-256 and runtime version with recordings. Replay verification
refuses a different engine build. Shared seeds, versions and fixed action cadence
remain necessary; a hash is compatibility evidence, not an authenticity signature.

## Consequences

- One built artifact supplies both graphical and headless execution. No Godot,
  Blender, Chromium, Python, package manager or coding-agent stack enters runtime.
- Browser and worker must deploy together. Changing source requires rebuilding the
  image; old recordings require their matching engine build for verification.
- Session count, input size, tick intervals, TTL, memory, CPU and process limits
  bound trusted simulation work. Runtime restart loses sessions; durable journals
  and episode failure states remain with the lab, rather than resuming unknown state.
- The measured ARM64 image is about186MiB, dominated by Node's executable. This
  favors one auditable simulation over a second implementation to reduce bytes.
  Base manifests support ARM64/AMD64; only ARM64 was exercised locally.
- Human versus model visible ghosts, additional games and vision remain later PRs.
