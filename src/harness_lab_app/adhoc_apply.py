"""Writing an approved change into the operator's folder (ADR-0095).

The only place the lab writes outside its own directory. The API mounts the
local sources root read-only and stays that way; the worker launches a
throwaway container that bind-mounts exactly one folder read-write — the
task's — plus the patch read-only, and runs ``adhoc_git.py`` inside it.

Docker-out-of-docker: bind sources are resolved by the host daemon, so both
mounts are host paths (``host_local_sources_root`` for the folder,
``host_repo_root`` for the patch and the script), never this container's
``/app`` or ``/host-sources``.

Concurrency is held twice: the ``AdhocRun.apply_state`` row (one apply per
folder, refused at the API) and a Redis ``SET NX`` lease keyed by the folder,
because two API workers can pass the row check at the same instant. File locks
are not an option on the Docker Desktop bind (they exclude nothing there).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any

from harness_lab_app.config import get_settings

LEASE_SECONDS = 300
_LEASE_PREFIX = "harness-lab:adhoc-apply:"
_SCRIPT_RELATIVE = Path("src") / "harness_lab_app" / "adhoc_git.py"


class ApplyRefused(RuntimeError):
    """The container ran and git refused, or the container could not run."""


def host_folder(source_path: str) -> str:
    """The operator's folder as the host daemon names it, fenced to the root."""
    settings = get_settings()
    host_root = Path(settings.host_local_sources_root or "")
    if not settings.host_local_sources_root:
        raise ApplyRefused(
            "HARNESS_LAB_HOST_LOCAL_SOURCES_ROOT is not set, so the lab cannot "
            "name your folder to Docker. Set HARNESS_LAB_LOCAL_SOURCES_DIR and "
            "restart the lab."
        )
    relative = Path(source_path.strip().strip("/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ApplyRefused(
            f"{source_path!r} is not a folder under the local sources root."
        )
    import os

    return os.path.normpath(str(host_root / relative))


def _lease_key(source_path: str) -> str:
    digest = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:24]
    return f"{_LEASE_PREFIX}{digest}"


@contextlib.contextmanager
def folder_lease(source_path: str):
    """Hold the folder for one apply; raise ApplyRefused when someone else does."""
    settings = get_settings()
    client: Any = None
    with contextlib.suppress(Exception):
        import redis

        client = redis.Redis.from_url(settings.redis_url)
    key = _lease_key(source_path)
    token = hashlib.sha256(f"{key}:{id(object())}".encode()).hexdigest()
    if client is not None:
        try:
            acquired = client.set(key, token, nx=True, ex=LEASE_SECONDS)
        except Exception:
            acquired = True  # no broker answer: the DB row check still holds
            client = None
        if not acquired:
            raise ApplyRefused("Another change is being applied to this folder.")
    try:
        yield
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                if client.get(key) == token.encode():
                    client.delete(key)


def run_in_container(
    action: str, source_path: str, run_id: str, base_commit: str
) -> dict[str, Any]:
    """Run ``adhoc_git.py <action>`` against the folder in a one-shot container.

    Detached, waited on, then read and removed — not ``remove=True``: docker-py
    discards a container's stdout when it exits non-zero, and a refusal (exit 1)
    is exactly when the JSON on stdout says *why*.

    It runs as the uid:gid that owns the folder, read off the folder itself, so
    the files git writes belong to the operator and not to root.
    """
    import docker

    settings = get_settings()
    host_repo_root = settings.host_repo_root or str(settings.repo_root)
    folder = host_folder(source_path)
    host_patch = str(Path(host_repo_root) / "data" / "runs" / run_id / "change.patch")
    host_script = str(Path(host_repo_root) / _SCRIPT_RELATIVE)
    client = docker.from_env()
    container = None
    try:
        container = client.containers.run(
            image=settings.adhoc_apply_image,
            command=[
                "sh",
                "-c",
                # Run as the folder's owner; `stat` reads it inside the mount.
                'exec setpriv --reuid "$(stat -c %u /repo)" '
                '--regid "$(stat -c %g /repo)" --clear-groups '
                'python /lab/adhoc_git.py "$@"',
                "adhoc-apply",
                action,
                "/repo",
                "/lab/change.patch",
                base_commit,
            ],
            entrypoint=[],
            volumes={
                folder: {"bind": "/repo", "mode": "rw"},
                host_patch: {"bind": "/lab/change.patch", "mode": "ro"},
                host_script: {"bind": "/lab/adhoc_git.py", "mode": "ro"},
            },
            working_dir="/repo",
            network_disabled=True,
            detach=True,
            labels={"harness-lab.adhoc-apply": run_id},
        )
        outcome = container.wait(timeout=600)
        stdout = container.logs(stdout=True, stderr=False) or b""
        stderr = container.logs(stdout=False, stderr=True) or b""
    except docker.errors.ImageNotFound as exc:
        raise ApplyRefused(
            f"Image {settings.adhoc_apply_image} not found. Build it with `make app-up`."
        ) from exc
    except docker.errors.APIError as exc:
        raise ApplyRefused(
            "Docker could not mount your folder. Check that Docker Desktop shares "
            f"it (Settings → Resources → File sharing): {exc.explanation or exc}"
        ) from exc
    finally:
        if container is not None:
            with contextlib.suppress(Exception):
                container.remove(force=True)
    parsed = _parse(stdout)
    if parsed is not None:
        return parsed
    code = (outcome or {}).get("StatusCode") if isinstance(outcome, dict) else None
    raise ApplyRefused(
        f"The apply container printed no result (exit {code}): "
        f"{(stderr or stdout).decode('utf-8', errors='replace')[-600:]}"
    )


def _parse(raw: bytes) -> dict[str, Any] | None:
    for line in reversed(raw.decode("utf-8", errors="replace").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            with contextlib.suppress(json.JSONDecodeError):
                value = json.loads(line)
                if isinstance(value, dict):
                    return value
    return None


def apply_locally(
    action: str, source_path: str, run_id: str, base_commit: str
) -> dict[str, Any]:
    """The subprocess backend: the folder is reachable directly (host mode)."""
    from dataclasses import asdict

    from harness_lab_app import adhoc_git
    from harness_lab_app.adhoc_service import patch_path
    from harness_lab_app.local_sources_service import resolve

    patch = patch_path(run_id).read_text(encoding="utf-8")
    result = adhoc_git.apply_patch(
        resolve(source_path), patch, base_commit, reverse=action == "undo"
    )
    return asdict(result)
