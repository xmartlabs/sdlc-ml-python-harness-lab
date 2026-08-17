"""Shared literals that appear in user-facing copy.

Example endpoints live here rather than inline because they were duplicated
across eight call sites (API errors, preflight failures, settings descriptors,
the UI). Inline copies drift, and the ones they replaced had drifted into a
specific developer's LAN addresses. The frontend keeps its own copy in
``frontend/src/constants.ts`` — two strings do not justify an endpoint.

The addresses are RFC-1918 and deliberately generic: they illustrate the
*shape* a user must type (host + port + the ``/v1`` suffix people forget),
which is the whole reason the examples exist.
"""

from __future__ import annotations

# LM Studio's OpenAI-compatible server, default port 1234.
EXAMPLE_LMSTUDIO_URL = "http://192.168.1.50:1234/v1"

# Ollama's OpenAI-compatible server, default port 11434.
EXAMPLE_OLLAMA_URL = "http://192.168.1.50:11434/v1"
