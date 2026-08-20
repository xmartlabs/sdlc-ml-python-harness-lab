# Development Hooks Setup

## Pre-commit bootstrap

The `setup-hooks` target now ensures required tooling is available before hook installation.

### Command

```bash
make setup-hooks
```

### What it does

1. Verifies the virtual environment exists (`make install` if needed).
2. Syncs development dependencies with `uv sync --group dev`.
3. Installs Git hooks with `pre-commit install`.

This avoids failures like `Failed to spawn: pre-commit` when the binary is not yet installed.
