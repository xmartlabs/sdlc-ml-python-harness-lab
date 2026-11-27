"""Bounded, tool-free Codex subscription transport for text game decisions.

The caller supplies an isolated auth-only directory. This adapter never discovers
personal credentials or falls back to API billing. Codex's system wrapper and
reasoning controls differ from LM Studio; provenance records that distinction.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_lab_app.gameplay.contracts import PolicyReply
from harness_lab_app.native_auth import scrub_billing_overrides

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_lab_app.gameplay.contracts import Observation

_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apps",
    "multi_agent",
    "multi_agent_v2",
    "memories",
)
_ALLOWED_ITEMS = {"agent_message", "reasoning", "todo_list"}
_MAX_STDOUT = 2 * 1024 * 1024
_MAX_STDERR = 64 * 1024


def _request(observation: Observation) -> tuple[str, dict[str, Any]]:
    controls = {
        name: {
            "type": "integer" if bounds.integer else "number",
            "minimum": bounds.minimum,
            "maximum": bounds.maximum,
        }
        for name, bounds in observation.controls.items()
    }
    schema = {
        "type": "object",
        "properties": {
            "version": {"type": "string", "const": "gameplay.v1"},
            "observation_id": {
                "type": "string",
                "const": observation.observation_id,
            },
            "controls": {
                "type": "object",
                "properties": controls,
                "required": list(controls),
                "additionalProperties": False,
            },
        },
        "required": ["version", "observation_id", "controls"],
        "additionalProperties": False,
    }
    prompt = (
        "Play the fixed environment using only its visible state. "
        "Follow observation.instructions. Choose every advertised control. "
        "Controls last ticks_to_advance simulation ticks; game time pauses "
        "during inference. Return only the requested JSON action, no explanation.\n"
        "OBSERVATION\n" + observation.model_dump_json()
    )
    return prompt, schema


def _execute(
    command: list[str], prompt: str, env: dict[str, str], cwd: Path, timeout: float
) -> str:
    """Bound both output streams and kill the entire child process group."""
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as source, selectors.DefaultSelector() as selector:
        source.write(prompt.encode())
        source.seek(0)
        # The caller supplies a trusted executable; arguments never reach a shell.
        process = subprocess.Popen(  # nosec B603
            command,
            cwd=cwd,
            env=env,
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Codex gameplay decision timed out")
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffer = buffers[key.data]
                    buffer.extend(chunk)
                    limit = _MAX_STDOUT if key.data == "stdout" else _MAX_STDERR
                    if len(buffer) > limit:
                        raise RuntimeError("Codex gameplay output limit exceeded")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Codex gameplay decision timed out")
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError("Codex gameplay decision timed out") from exc
            if process.returncode:
                # Provider errors may contain credential-bearing details. Never
                # copy stderr into game recordings or user-facing exceptions.
                raise RuntimeError("Codex gameplay request failed")
            return buffers["stdout"].decode("utf-8", errors="strict")
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            process.stdout.close()
            process.stderr.close()


def _parse(output: str) -> tuple[str, dict[str, JsonValue]]:
    action: str | None = None
    completed = False
    usage: dict[str, JsonValue] = {}
    response_model: str | None = None
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Codex gameplay returned malformed events") from exc
        if not isinstance(event, dict):
            raise RuntimeError("Codex gameplay returned malformed events")
        kind = event.get("type")
        if kind in {"turn.failed", "error"}:
            raise RuntimeError("Codex gameplay request failed")
        item = event.get("item")
        if isinstance(item, dict):
            if item.get("type") not in _ALLOWED_ITEMS:
                raise RuntimeError("Codex gameplay attempted a forbidden tool action")
            if kind == "item.completed" and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    action = text
        if kind == "turn.completed":
            completed = True
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                usage = {
                    key: value
                    for key, value in raw_usage.items()
                    if isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                }
        if isinstance(event.get("model"), str):
            response_model = event["model"]
    if not completed or action is None:
        raise RuntimeError("Codex gameplay returned no completed action")
    if len(action) > 16_384:
        raise RuntimeError("Codex gameplay action output limit exceeded")
    # Preserve invalid JSON/actions verbatim for the episode runner's normal
    # invalid_action outcome; do not repair controls or observation references.
    return action, {
        "prompt_tokens": usage.get("input_tokens"),
        "cached_prompt_tokens": usage.get("cached_input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
        "response_model": response_model,
    }


def _return_refreshed_auth(source: Path, staged: Path, original: bytes) -> None:
    """Return only a valid refreshed subscription credential to task-owned auth.

    Generated CLI configuration never returns. Preserve a concurrent source
    change rather than overwrite it; failed refresh persistence is non-fatal.
    """
    temporary: Path | None = None
    try:
        fresh = staged.read_bytes()
        if fresh == original or source.is_symlink() or source.read_bytes() != original:
            return
        data = json.loads(fresh)
        if not isinstance(data, dict) or data.get("OPENAI_API_KEY"):
            return
        tokens = data.get("tokens")
        if not isinstance(tokens, dict) or not tokens.get("refresh_token"):
            return
        with tempfile.NamedTemporaryFile(dir=source.parent, delete=False) as target:
            temporary = Path(target.name)
            target.write(fresh)
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, source)
    except (OSError, ValueError):
        # Never leak credential bytes or turn a valid game action into a retry.
        pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass
class CodexGameplayPolicy:
    auth_directory: Path
    executable: str
    model_id: str = "gpt-6.1-sol"
    reasoning_effort: str = "low"
    cli_version: str | None = None

    def __post_init__(self) -> None:
        self.auth_directory = self.auth_directory.resolve()
        if (
            not (self.auth_directory / "auth.json").is_file()
            or (self.auth_directory / "auth.json").is_symlink()
        ):
            raise ValueError("Codex gameplay requires an explicit auth.json directory")
        forbidden = {"config.toml", "AGENTS.md", "skills", "plugins", "rules", "hooks"}
        if any((self.auth_directory / name).exists() for name in forbidden):
            raise ValueError("Codex gameplay requires an isolated auth-only directory")

    @property
    def provenance(self) -> dict[str, JsonValue]:
        return {
            "provider": "codex-cli-subscription",
            "requested_model": self.model_id,
            "effective_model": None,
            "cli_version": self.cli_version,
            "reasoning_effort": self.reasoning_effort,
            "requested_temperature": None,
            "max_output_tokens": None,
            "response_format": "json_schema",
            "policy_version": "structured-action.codex.v1",
            "billing_mode": "subscription",
            "cost_source": "subscription_quota",
            "wrapper_caveat": "Codex system wrapper and reasoning differ from LM Studio",
        }

    def __call__(self, observation: Observation, timeout: float) -> PolicyReply:
        if timeout <= 0:
            raise TimeoutError("Codex gameplay decision timed out")
        prompt, schema = _request(observation)
        with tempfile.TemporaryDirectory(prefix="gameplay-codex-") as temporary:
            root = Path(temporary)
            workspace, home, codex_home = (
                root / "workspace",
                root / "home",
                root / "codex",
            )
            workspace.mkdir()
            home.mkdir()
            codex_home.mkdir(mode=0o700)
            source_auth = self.auth_directory / "auth.json"
            original_auth = source_auth.read_bytes()
            staged_auth = codex_home / "auth.json"
            staged_auth.write_bytes(original_auth)
            staged_auth.chmod(0o600)
            schema_path = root / "action-schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            env = scrub_billing_overrides(dict(os.environ))
            for name in list(env):
                if name.startswith(("CODEX_", "OPENAI_")):
                    env.pop(name)
            env.update(
                HOME=str(home),
                XDG_CONFIG_HOME=str(home / ".config"),
                CODEX_HOME=str(codex_home),
            )
            command = [
                self.executable,
                "exec",
                "--json",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-m",
                self.model_id,
                "-c",
                f'model_reasoning_effort="{self.reasoning_effort}"',
                "-c",
                'web_search="disabled"',
                "--output-schema",
                str(schema_path),
            ]
            for feature in _DISABLED_FEATURES:
                command += ["--disable", feature]
            command.append("-")
            try:
                output = _execute(command, prompt, env, workspace, timeout)
            finally:
                _return_refreshed_auth(source_auth, staged_auth, original_auth)
        action, usage = _parse(output)
        return PolicyReply(action_json=action, provenance={**self.provenance, **usage})
