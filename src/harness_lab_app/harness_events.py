"""Normalize the event streams of the native agent CLIs into one shape.

Three harnesses emit a machine-readable stream per step, each in its own
dialect:

* ``claude -p --output-format stream-json`` — the lab's reference shape:
  ``system/init``, ``assistant`` (text + ``tool_use`` blocks), ``user``
  (``tool_result`` blocks), ``result`` (usage, cost, turns).
* ``codex exec --json`` (codex-cli 0.171.0, ``exec/src/exec_events.rs``):
  ``thread.started``, ``turn.started``, ``turn.completed{usage}``,
  ``turn.failed{error}``, ``item.started|updated|completed{item}``, ``error``.
* ``gemini -p --output-format stream-json`` (gemini-cli 0.70.0): ``init``,
  ``message``, ``tool_use``, ``tool_result``, ``error``, ``result{stats}``.
* ``agy -p --output-format stream-json`` (Antigravity CLI 1.5.14): ``init``,
  ``step_update`` (text deltas, tool steps), ``result{usage}``.

Every reader the lab already has (attribution, trajectory, score evaluators,
run progress, the Langfuse exporter, the per-step reward) speaks the claude
shape. Rather than fork each of them per CLI, the runner writes the CLI's raw
stream untouched to ``step-N.raw.jsonl`` (provenance) and a translation into
the claude shape to ``step-N.events.jsonl`` (what the readers read). Tool names
are mapped onto the claude vocabulary those readers already understand, so
"files read", "files edited", "bash commands" and "skills opened" mean the same
thing for all three harnesses.

Two rules keep the translation honest:

* **Nothing is invented.** A field the source does not carry is absent, never
  zero: a subscription run's ``result`` carries ``total_cost_usd: null`` and a
  ``billing_mode``, because a plan's quota has no per-token price.
* **The raw stream stays.** The translation is a view; the bytes the CLI wrote
  are archived next to it, stamped with their arrival time.

Pure functions only: no I/O, so the runner and the tests share one code path.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Harnesses whose step stream is translated (claude's is already the shape).
TRANSLATED_HARNESSES: frozenset[str] = frozenset({"codex", "gemini", "antigravity_cli"})

#: Suffix of the untouched per-step CLI stream, beside ``step-N.events.jsonl``.
RAW_EVENTS_SUFFIX = ".raw.jsonl"

# Gemini CLI tool names (packages/core tool-names) → the claude tool vocabulary
# the attribution and trajectory parsers already read. Anything not listed keeps
# its own name under a ``gemini:`` prefix, so it is counted as a tool call
# without pretending to be something it is not.
_GEMINI_TOOLS: dict[str, str] = {
    "read_file": "Read",
    "read_many_files": "Read",
    "write_file": "Write",
    "replace": "Edit",
    "run_shell_command": "Bash",
    "glob": "Glob",
    "grep_search": "Grep",
    "list_directory": "LS",
    "activate_skill": "Skill",
    "web_fetch": "WebFetch",
    "google_web_search": "WebSearch",
    "invoke_agent": "Agent",
    "write_todos": "TodoWrite",
}


def _loads(raw: str) -> dict[str, Any] | None:
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return event if isinstance(event, dict) else None


def _obj(value: Any) -> dict[str, Any]:
    """``value`` when it is a JSON object, else an empty one (typed for mypy)."""
    return value if isinstance(value, dict) else {}


def _dump(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def _stamp(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Carry the runner's arrival stamp (or the CLI's own clock) across."""
    received = source.get("_received_at") or source.get("timestamp")
    if received:
        return {"_received_at": received, **target}
    return target


def _assistant(blocks: list[dict[str, Any]], message_id: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"id": message_id, "role": "assistant", "content": blocks},
    }


def _tool_result(tool_use_id: str, content: str, is_error: bool) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"role": "user", "content": [block]}}


# ---------------------------------------------------------------------------
# codex exec --json
# ---------------------------------------------------------------------------


def _codex_item_events(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Claude-shaped events for one *completed* codex item."""
    item_id = str(item.get("id") or "")
    kind = item.get("type")
    if kind == "agent_message":
        text = str(item.get("text") or "")
        return [_assistant([{"type": "text", "text": text}], item_id)] if text else []
    if kind == "command_execution":
        status = str(item.get("status") or "")
        exit_code = item.get("exit_code")
        failed = status in {"failed", "declined"} or (
            isinstance(exit_code, int) and exit_code != 0
        )
        use = {
            "type": "tool_use",
            "id": item_id,
            "name": "Bash",
            "input": {"command": str(item.get("command") or "")},
        }
        output = str(item.get("aggregated_output") or "")
        return [
            _assistant([use], item_id),
            _tool_result(item_id, output, failed),
        ]
    if kind == "file_change":
        changes = item.get("changes") or []
        failed = str(item.get("status") or "") == "failed"
        blocks = []
        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                continue
            # `add` creates a file, `update`/`delete` change one that existed.
            name = "Write" if change.get("kind") == "add" else "Edit"
            blocks.append(
                {
                    "type": "tool_use",
                    "id": f"{item_id}:{index}",
                    "name": name,
                    "input": {
                        "file_path": str(change.get("path") or ""),
                        "change_kind": str(change.get("kind") or ""),
                    },
                }
            )
        if not blocks:
            return []
        events = [_assistant(blocks, item_id)]
        events.extend(_tool_result(str(block["id"]), "", failed) for block in blocks)
        return events
    if kind == "mcp_tool_call":
        name = f"mcp__{item.get('server') or '?'}__{item.get('tool') or '?'}"
        use = {
            "type": "tool_use",
            "id": item_id,
            "name": name,
            "input": item.get("arguments") or {},
        }
        failed = item.get("error") is not None or item.get("status") == "failed"
        return [_assistant([use], item_id), _tool_result(item_id, "", failed)]
    if kind == "web_search":
        use = {
            "type": "tool_use",
            "id": item_id,
            "name": "WebSearch",
            "input": {"query": str(item.get("query") or "")},
        }
        return [_assistant([use], item_id), _tool_result(item_id, "", False)]
    if kind == "collab_tool_call":
        use = {
            "type": "tool_use",
            "id": item_id,
            "name": "Agent",
            "input": {
                "subagent_type": str(item.get("tool") or "agent"),
                "prompt": str(item.get("prompt") or ""),
            },
        }
        failed = item.get("status") == "failed"
        return [_assistant([use], item_id), _tool_result(item_id, "", failed)]
    if kind == "todo_list":
        use = {
            "type": "tool_use",
            "id": item_id,
            "name": "TodoWrite",
            "input": {"todos": item.get("items") or []},
        }
        return [_assistant([use], item_id), _tool_result(item_id, "", False)]
    # `reasoning` (private thoughts) and `error` items (reconnect notices) are
    # not agent actions; the raw stream keeps them.
    return []


def codex_to_claude(
    lines: list[str], *, billing_mode: str = "subscription"
) -> list[str]:
    """Translate one step's ``codex exec --json`` stream into claude events.

    Usage is read from the **last** ``turn.completed``: codex reports the
    thread's cumulative total there, not the turn's own
    (``event_processor_with_jsonl_output.rs`` ``usage_from_last_total``), so
    summing the turns would double-count. ``input_tokens`` includes the cached
    part in codex's accounting; claude's excludes it, so the cached share is
    moved to ``cache_read_input_tokens``. Reasoning tokens are already inside
    ``output_tokens`` and are kept beside it, not added.
    """
    out: list[str] = []
    thread_id = ""
    turns = 0
    usage: dict[str, int] | None = None
    final_text = ""
    failure = ""
    first_stamp: dict[str, Any] | None = None
    last_stamp: dict[str, Any] = {}
    for raw in lines:
        event = _loads(raw)
        if event is None:
            continue
        if first_stamp is None:
            first_stamp = event
        last_stamp = event
        kind = event.get("type")
        if kind == "thread.started":
            thread_id = str(event.get("thread_id") or "")
            out.append(
                _dump(
                    _stamp(
                        {
                            "type": "system",
                            "subtype": "init",
                            "session_id": thread_id,
                            "harness": "codex",
                        },
                        event,
                    )
                )
            )
        elif kind == "turn.started":
            turns += 1
        elif kind == "turn.completed":
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                usage = {
                    k: int(v or 0) for k, v in raw_usage.items() if isinstance(v, int)
                }
        elif kind == "turn.failed":
            error = event.get("error") or {}
            failure = str(error.get("message") if isinstance(error, dict) else error)
        elif kind == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") == "agent_message" and item.get("text"):
                final_text = str(item["text"])
            for translated in _codex_item_events(item):
                out.append(_dump(_stamp(translated, event)))
    if first_stamp is None:
        return out
    result: dict[str, Any] = {
        "type": "result",
        "subtype": "error" if failure else "success",
        "is_error": bool(failure),
        "session_id": thread_id,
        "num_turns": turns,
        "result": failure or final_text,
        "total_cost_usd": None,
        "billing_mode": billing_mode,
        "harness": "codex",
    }
    if usage is not None:
        total_input = usage.get("input_tokens", 0)
        cached = usage.get("cached_input_tokens", 0)
        result["usage"] = {
            "input_tokens": max(0, total_input - cached),
            "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": usage.get("cache_write_input_tokens", 0),
            # Reasoning is already part of codex's `output_tokens`
            # (protocol.rs `blended_total = non_cached_input + output_tokens`),
            # so it is recorded beside it, never added to it.
            "output_tokens": usage.get("output_tokens", 0),
            "reasoning_output_tokens": usage.get("reasoning_output_tokens", 0),
        }
    out.append(_dump(_stamp(result, last_stamp)))
    return out


# ---------------------------------------------------------------------------
# gemini -p --output-format stream-json
# ---------------------------------------------------------------------------


def _gemini_input(tool: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Gemini tool parameters, renamed where the claude parsers expect a key."""
    payload = dict(parameters)
    if tool == "activate_skill" and "name" in payload:
        payload["skill"] = payload["name"]
    if tool == "read_many_files" and "file_path" not in payload:
        paths = payload.get("include") or payload.get("paths") or []
        if isinstance(paths, list) and paths:
            payload["file_path"] = str(paths[0])
    if tool == "list_directory" and "dir_path" in payload:
        payload.setdefault("path", payload["dir_path"])
    return payload


def gemini_to_claude(
    lines: list[str], *, billing_mode: str = "subscription"
) -> list[str]:
    """Translate one step's gemini ``stream-json`` into claude events.

    Gemini streams assistant text as ``delta`` chunks; consecutive chunks are
    joined into one text block. ``result.stats.input_tokens`` is the prompt
    *including* the cache and ``stats.input`` the uncached part, which is the
    claude meaning of ``input_tokens``.
    """
    out: list[str] = []
    session_id = ""
    model = ""
    text_parts: list[str] = []
    text_stamp: dict[str, Any] = {}
    final_text = ""
    message_index = 0
    turns = 0
    last_was_tool = True

    def flush_text() -> None:
        nonlocal text_parts, final_text, message_index
        text = "".join(text_parts).strip()
        text_parts = []
        if not text:
            return
        final_text = text
        message_index += 1
        out.append(
            _dump(
                _stamp(
                    _assistant(
                        [{"type": "text", "text": text}], f"gemini-msg-{message_index}"
                    ),
                    text_stamp,
                )
            )
        )

    saw_any = False
    for raw in lines:
        event = _loads(raw)
        if event is None:
            continue
        saw_any = True
        kind = event.get("type")
        if kind == "init":
            session_id = str(event.get("session_id") or "")
            model = str(event.get("model") or "")
            out.append(
                _dump(
                    _stamp(
                        {
                            "type": "system",
                            "subtype": "init",
                            "session_id": session_id,
                            "model": model,
                            "harness": "gemini",
                        },
                        event,
                    )
                )
            )
        elif kind == "message":
            if event.get("role") != "assistant":
                continue
            if last_was_tool:
                turns += 1
                last_was_tool = False
            if not text_parts:
                text_stamp = event
            text_parts.append(str(event.get("content") or ""))
        elif kind == "tool_use":
            flush_text()
            if last_was_tool:
                turns += 1
            last_was_tool = True
            tool = str(event.get("tool_name") or "")
            tool_id = str(event.get("tool_id") or "")
            parameters = event.get("parameters")
            name = _GEMINI_TOOLS.get(tool, f"gemini:{tool}" if tool else "")
            if not name:
                continue
            block = {
                "type": "tool_use",
                "id": tool_id,
                "name": name,
                "input": _gemini_input(
                    tool, parameters if isinstance(parameters, dict) else {}
                ),
            }
            out.append(_dump(_stamp(_assistant([block], tool_id), event)))
        elif kind == "tool_result":
            flush_text()
            error = event.get("error")
            content = str(event.get("output") or "")
            if isinstance(error, dict) and error.get("message"):
                content = str(error["message"])
            out.append(
                _dump(
                    _stamp(
                        _tool_result(
                            str(event.get("tool_id") or ""),
                            content,
                            event.get("status") == "error",
                        ),
                        event,
                    )
                )
            )
        elif kind == "result":
            flush_text()
            stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
            error = event.get("error") if isinstance(event.get("error"), dict) else None
            failed = event.get("status") == "error"
            result: dict[str, Any] = {
                "type": "result",
                "subtype": "error" if failed else "success",
                "is_error": failed,
                "session_id": session_id,
                "num_turns": turns,
                "result": str((error or {}).get("message") or "")
                if failed
                else final_text,
                "total_cost_usd": None,
                "billing_mode": billing_mode,
                "harness": "gemini",
            }
            if stats:
                if isinstance(stats.get("duration_ms"), int):
                    result["duration_ms"] = stats["duration_ms"]
                result["usage"] = {
                    "input_tokens": int(stats.get("input") or 0),
                    "cache_read_input_tokens": int(stats.get("cached") or 0),
                    "output_tokens": int(stats.get("output_tokens") or 0),
                }
                models = stats.get("models")
                if isinstance(models, dict) and models:
                    result["modelUsage"] = {
                        str(name): {
                            "inputTokens": int((m or {}).get("input") or 0),
                            "outputTokens": int((m or {}).get("output_tokens") or 0),
                            "cacheReadInputTokens": int((m or {}).get("cached") or 0),
                        }
                        for name, m in models.items()
                        if isinstance(m, dict)
                    }
            out.append(_dump(_stamp(result, event)))
    if saw_any:
        flush_text()
    return out


# ---------------------------------------------------------------------------
# agy -p --output-format stream-json (Antigravity CLI, antigravity_cli)
# ---------------------------------------------------------------------------

# agy 1.5.14 tool names → the claude vocabulary. The wire names (what the model
# calls, what `tool_name` reports) were read from a captured request body and
# stream; the `init.tools` aliases (`view_file`, `write_to_file`, ...) are
# mapped too, since a later CLI may report those instead. Anything else keeps
# its own name under an `agy:` prefix: counted as a tool call, never mistaken
# for an edit or a read it is not.
_AGY_TOOLS: dict[str, str] = {
    "read_file": "Read",
    "view_file": "Read",
    "write_file": "Write",
    "write_to_file": "Write",
    "edit_file": "Edit",
    "replace_file_content": "Edit",
    "multi_replace_file_content": "MultiEdit",
    "sed_file": "Edit",
    "notebook_edit": "NotebookEdit",
    "run_command": "Bash",
    "grep_search": "Grep",
    "find_by_name": "Glob",
    "list_dir": "LS",
    "search_web": "WebSearch",
    "read_url_content": "WebFetch",
    "invoke_subagent": "Agent",
    "browser_subagent": "Agent",
}

# `run_command` reports a failed command inside a DONE step, in its output:
# "Command exited with code 1." (agy 1.5.14).
_AGY_EXIT = re.compile(r"Command exited with code (-?\d+)")


def _agy_input(tool: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """agy parameters, plus the keys the claude parsers read.

    agy echoes only the path (``Path``) or the command line (``CommandLine``)
    of a call in ``tool_info.parameters``; contents never reach the stream.
    """
    payload = dict(parameters)
    path = payload.get("Path") or payload.get("AbsolutePath") or payload.get("File")
    if path:
        payload.setdefault("file_path", str(path))
        payload.setdefault("path", str(path))
    command = payload.get("CommandLine")
    if command:
        payload.setdefault("command", str(command))
    if tool in {"invoke_subagent", "browser_subagent"}:
        payload.setdefault(
            "subagent_type", str(payload.get("TypeName") or payload.get("Role") or tool)
        )
    if tool == "search_web" and "query" in payload:
        payload.setdefault("query", payload["query"])
    return payload


def _agy_tool_failed(state: str, info: dict[str, Any]) -> tuple[bool, str]:
    """(failed, content) for one finished agy tool step."""
    error = info.get("error")
    output = str(info.get("output") or "")
    if state == "ERROR" or (isinstance(error, dict) and error):
        message = (
            str((error or {}).get("message") or "") if isinstance(error, dict) else ""
        )
        return True, message or output
    exit_code = _AGY_EXIT.search(output)
    if exit_code and int(exit_code.group(1)) != 0:
        return True, output
    return False, output


def agy_usage(raw: Any) -> dict[str, int] | None:
    """An agy ``usage`` block in the claude meaning of each field.

    Verified against a mock with known token counts (agy 1.5.14):
    ``input_tokens`` already excludes the cached prompt (claude's meaning),
    ``cache_read_tokens`` is the cached part, and ``output_tokens`` includes
    ``thinking_tokens`` — so thinking is recorded beside output, never added.
    """
    if not isinstance(raw, dict):
        return None
    numbers = {k: v for k, v in raw.items() if isinstance(v, int)}
    if not numbers:
        return None
    return {
        "input_tokens": numbers.get("input_tokens", 0),
        "cache_read_input_tokens": numbers.get("cache_read_tokens", 0),
        "output_tokens": numbers.get("output_tokens", 0),
        "reasoning_output_tokens": numbers.get("thinking_tokens", 0),
    }


def antigravity_to_claude(
    lines: list[str], *, billing_mode: str = "api", model: str = ""
) -> list[str]:
    """Translate one step's ``agy --output-format stream-json`` into claude events.

    The stream is ``init``, then ``step_update`` events, then one ``result``
    (antigravity.google/docs/cli/headless, verified on agy 1.5.14):

    * Assistant text arrives as ``ACTIVE`` ``text_delta`` chunks closed by a
      ``DONE`` that carries only the tail (often ``"\\n"``); chunks are joined
      per ``step_index``.
    * A tool step reports ``ACTIVE`` then ``DONE`` (or ``ERROR``); only the
      finished one is translated, as a ``tool_use`` plus its ``tool_result``.
    * ``result.usage`` is the session's total (the sum of the steps'), so it is
      the only usage carried into the translated ``result``.

    No agy event names the Gemini API model that served the turn; ``init``
    names the requested slug when ``--model`` was passed, and ``model`` (the
    run's configured slug) is the fallback. A field agy does not emit stays
    absent.
    """
    out: list[str] = []
    conversation = ""
    detected = model
    texts: dict[int, list[str]] = {}
    text_stamps: dict[int, dict[str, Any]] = {}
    final_text = ""
    saw_any = False
    result_seen = False
    # Per-step usage, summed: the session's total when no `result` arrives
    # (the watchdog killed the step), so its tokens are not lost.
    step_usage: dict[str, int] = {}
    steps_with_usage = 0
    last_event: dict[str, Any] = {}

    def flush(index: int) -> None:
        nonlocal final_text
        text = "".join(texts.pop(index, [])).strip()
        stamp = text_stamps.pop(index, {})
        if not text:
            return
        final_text = text
        out.append(
            _dump(
                _stamp(
                    _assistant([{"type": "text", "text": text}], f"agy-msg-{index}"),
                    stamp,
                )
            )
        )

    for raw in lines:
        event = _loads(raw)
        if event is None:
            continue
        saw_any = True
        kind = event.get("event")
        if kind == "init":
            body = _obj(event.get("init"))
            conversation = str(event.get("conversation_id") or "")
            detected = str(body.get("model") or "") or detected
            init: dict[str, Any] = {
                "type": "system",
                "subtype": "init",
                "session_id": conversation,
                "harness": "antigravity_cli",
                "permission_mode": str(body.get("permission_mode") or ""),
            }
            if detected:
                init["model"] = detected
            out.append(_dump(_stamp(init, event)))
        elif kind == "step_update":
            step = event.get("step_update")
            if not isinstance(step, dict):
                continue
            last_event = event
            index = int(step.get("step_index") or 0)
            if str(step.get("state") or "") == "DONE":
                partial = agy_usage(step.get("usage"))
                if partial is not None:
                    steps_with_usage += 1
                    for name, value in partial.items():
                        step_usage[name] = step_usage.get(name, 0) + value
            state = str(step.get("state") or "")
            step_type = str(step.get("step_type") or "")
            if step_type == "agent_response":
                delta = step.get("text_delta")
                if isinstance(delta, str) and delta:
                    if index not in texts:
                        text_stamps[index] = event
                    texts.setdefault(index, []).append(delta)
                if state in {"DONE", "ERROR"}:
                    flush(index)
            elif step_type in {"tool", "subagent"} and state in {"DONE", "ERROR"}:
                info = _obj(step.get("tool_info"))
                tool = str(step.get("tool_name") or info.get("name") or "")
                if not tool and step_type == "subagent":
                    tool = "invoke_subagent"
                if not tool:
                    continue
                parameters = info.get("parameters")
                if step_type == "subagent":
                    # A subagent step reports `subagent_info` (type, role,
                    # conversation), not `tool_info`.
                    sub = _obj(step.get("subagent_info"))
                    first = next(
                        (s for s in sub.get("subagents") or [] if isinstance(s, dict)),
                        {},
                    )
                    parameters = {
                        **_obj(parameters),
                        **{
                            key: first[key]
                            for key in ("type_name", "role")
                            if first.get(key)
                        },
                    }
                    if first.get("type_name"):
                        parameters.setdefault("TypeName", first["type_name"])
                tool_id = f"agy-{conversation[:8]}-{index}"
                name = _AGY_TOOLS.get(tool, f"agy:{tool}")
                block = {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": name,
                    "input": _agy_input(
                        tool, parameters if isinstance(parameters, dict) else {}
                    ),
                }
                failed, content = _agy_tool_failed(state, info)
                out.append(_dump(_stamp(_assistant([block], tool_id), event)))
                out.append(_dump(_stamp(_tool_result(tool_id, content, failed), event)))
        elif kind == "result":
            for index in sorted(texts):
                flush(index)
            body = _obj(event.get("result"))
            status = str(body.get("status") or "")
            failed = status != "SUCCESS"
            response = str(body.get("response") or "").strip()
            result: dict[str, Any] = {
                "type": "result",
                "subtype": "success" if not failed else "error",
                "is_error": failed,
                "session_id": str(body.get("conversation_id") or conversation),
                "num_turns": int(body.get("num_turns") or 0),
                "result": str(body.get("error") or status)
                if failed
                else (response or final_text),
                "total_cost_usd": None,
                "billing_mode": billing_mode,
                "harness": "antigravity_cli",
                "status": status,
            }
            if detected:
                result["model"] = detected
            seconds = body.get("duration_seconds")
            if isinstance(seconds, (int, float)):
                result["duration_ms"] = round(float(seconds) * 1000)
            usage = agy_usage(body.get("usage"))
            if usage is not None:
                result["usage"] = usage
                if detected:
                    result["modelUsage"] = {
                        detected: {
                            "inputTokens": usage["input_tokens"],
                            "outputTokens": usage["output_tokens"],
                            "cacheReadInputTokens": usage["cache_read_input_tokens"],
                        }
                    }
            result_seen = True
            out.append(_dump(_stamp(result, event)))
    if saw_any and not result_seen:
        for index in sorted(texts):
            flush(index)
        if steps_with_usage:
            # No `result`: the step was cut. What it spent is still known,
            # step by step; the outcome is not, so it is an error result.
            partial_result: dict[str, Any] = {
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "session_id": conversation,
                "num_turns": 0,
                "result": "agy stream ended without a result (step interrupted)",
                "total_cost_usd": None,
                "billing_mode": billing_mode,
                "harness": "antigravity_cli",
                "usage": step_usage,
                "usage_partial": True,
            }
            if detected:
                partial_result["model"] = detected
            out.append(_dump(_stamp(partial_result, last_event)))
    return out


def translate(
    harness: str,
    lines: list[str],
    *,
    billing_mode: str = "",
    model: str = "",
) -> list[str]:
    """The claude-shaped view of one step's raw stream, for ``harness``.

    ``billing_mode`` empty keeps each harness's own default: codex and gemini
    run on a subscription, antigravity_cli on a Gemini API key unless the
    caller says the run used the Google sign-in (ADR-0097).
    """
    if harness == "codex":
        return codex_to_claude(lines, billing_mode=billing_mode or "subscription")
    if harness == "gemini":
        return gemini_to_claude(lines, billing_mode=billing_mode or "subscription")
    if harness == "antigravity_cli":
        mode = billing_mode if billing_mode in {"api", "subscription"} else "api"
        return antigravity_to_claude(lines, billing_mode=mode, model=model)
    return list(lines)


# ---------------------------------------------------------------------------
# Readable one-liners for the step log (the UI and the watchdog read these)
# ---------------------------------------------------------------------------

#: What the codex/gemini renderers print when a request is in flight — the
#: same convention as claude's ``# awaiting model`` (run_failures reads it).
AWAITING_MARKER = "# awaiting model"


def render_codex_event(raw_line: str) -> str | None:
    """One readable line for a codex JSONL event (``None`` = drop)."""
    event = _loads(raw_line)
    if event is None:
        return raw_line.rstrip("\n") + "\n"
    kind = event.get("type")
    if kind == "thread.started":
        return f"# codex session init (thread {event.get('thread_id', '?')})\n"
    if kind == "turn.started":
        return f"{AWAITING_MARKER}\n"
    if kind == "turn.failed":
        error = event.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else error
        return f"# turn failed: API Error: {message}\n"
    if kind == "error":
        return f"# stream error: {event.get('message', '')}\n"
    if kind == "turn.completed":
        usage = event.get("usage") or {}
        return (
            f"# turn completed: input={usage.get('input_tokens', '?')} "
            f"cached={usage.get('cached_input_tokens', '?')} "
            f"output={usage.get('output_tokens', '?')}\n"
        )
    if kind == "item.started":
        item = event.get("item") or {}
        if item.get("type") == "command_execution":
            command = str(item.get("command") or "").replace("\n", " ")[:160]
            return f"→ Bash({command})\n"
        return None
    if kind == "item.completed":
        item = event.get("item") or {}
        item_type = item.get("type")
        if item_type == "agent_message":
            return str(item.get("text") or "").rstrip() + "\n"
        if item_type == "file_change":
            paths = ", ".join(
                str(c.get("path"))
                for c in item.get("changes") or []
                if isinstance(c, dict)
            )
            return f"→ Edit({paths[:160]})\n"
        if item_type == "command_execution":
            return f"{AWAITING_MARKER} (Bash exit {item.get('exit_code')})\n"
        if item_type == "mcp_tool_call":
            return f"→ mcp__{item.get('server')}__{item.get('tool')}\n"
        if item_type == "web_search":
            return f"→ WebSearch({str(item.get('query') or '')[:160]})\n"
    return None


def render_gemini_event(raw_line: str) -> str | None:
    """One readable line for a gemini stream-json event (``None`` = drop)."""
    event = _loads(raw_line)
    if event is None:
        return raw_line.rstrip("\n") + "\n"
    kind = event.get("type")
    if kind == "init":
        return f"# gemini session init (model {event.get('model', '?')})\n"
    if kind == "message":
        if event.get("role") != "assistant":
            return None
        text = str(event.get("content") or "")
        return text if text else None
    if kind == "tool_use":
        params = event.get("parameters") or {}
        arg = ""
        if isinstance(params, dict):
            arg = str(
                params.get("file_path")
                or params.get("command")
                or params.get("name")
                or params.get("pattern")
                or params.get("dir_path")
                or ""
            )
        arg = arg.replace("\n", " ")[:160]
        name = _GEMINI_TOOLS.get(
            str(event.get("tool_name") or ""), event.get("tool_name")
        )
        return f"\n→ {name}({arg})\n"
    if kind == "tool_result":
        return f"{AWAITING_MARKER}\n"
    if kind == "error":
        return f"# stream error: {event.get('message', '')}\n"
    if kind == "result":
        stats = event.get("stats") or {}
        status = event.get("status", "?")
        if status == "error":
            error = event.get("error") or {}
            return f"# result: error API Error: {error.get('message', '')}\n"
        return (
            f"# result: {status} tool_calls={stats.get('tool_calls', '?')} "
            f"input={stats.get('input_tokens', '?')} "
            f"output={stats.get('output_tokens', '?')} "
            f"duration_ms={stats.get('duration_ms', '?')}\n"
        )
    return None


def render_antigravity_event(raw_line: str) -> str | None:
    """One readable line for an agy stream-json event (``None`` = drop).

    Non-JSON lines (agy's ``error: …`` and ``AGY_ERROR: {…}`` on stderr, merged
    into the step log by the runner) pass through: they are the provider's own
    words for a failure, and ``harness_error_cause`` reads the ``API Error``
    line rendered from the ``result`` below.
    """
    event = _loads(raw_line)
    if event is None:
        return raw_line.rstrip("\n") + "\n"
    kind = event.get("event")
    if kind == "init":
        body = _obj(event.get("init"))
        return (
            f"# antigravity_cli session init (model {body.get('model') or 'CLI default'}, "
            f"permissions {body.get('permission_mode', '?')})\n{AWAITING_MARKER}\n"
        )
    if kind == "step_update":
        step = _obj(event.get("step_update"))
        state = step.get("state")
        step_type = step.get("step_type")
        if step_type == "agent_response":
            delta = step.get("text_delta")
            return delta if isinstance(delta, str) and delta else None
        if step_type in {"tool", "subagent"}:
            info = _obj(step.get("tool_info"))
            tool = str(step.get("tool_name") or info.get("name") or "")
            params = _obj(info.get("parameters"))
            arg = str(params.get("Path") or params.get("CommandLine") or "")
            arg = arg.replace("\n", " ")[:160]
            name = _AGY_TOOLS.get(tool, tool)
            if state == "ACTIVE":
                return f"\n→ {name}({arg})\n"
            if state == "ERROR":
                error = _obj(info.get("error"))
                return f"# tool error: {str(error.get('message') or '')[:300]}\n{AWAITING_MARKER}\n"
            return f"{AWAITING_MARKER}\n"
        if step_type == "error_message":
            return "# stream error: the agent reported an error step\n"
        return None
    if kind == "result":
        body = _obj(event.get("result"))
        status = body.get("status", "?")
        usage = _obj(body.get("usage"))
        if status != "SUCCESS":
            return f"# result: {status} API Error: {body.get('error', '')}\n"
        return (
            f"# result: {status} turns={body.get('num_turns', '?')} "
            f"input={usage.get('input_tokens', '?')} "
            f"cache_read={usage.get('cache_read_tokens', '?')} "
            f"output={usage.get('output_tokens', '?')} "
            f"thinking={usage.get('thinking_tokens', '?')} "
            f"duration_s={body.get('duration_seconds', '?')}\n"
        )
    return None


def render_event(harness: str, raw_line: str) -> str | None:
    """Dispatch to the per-harness renderer (codex | gemini | antigravity_cli)."""
    if harness == "codex":
        return render_codex_event(raw_line)
    if harness == "gemini":
        return render_gemini_event(raw_line)
    if harness == "antigravity_cli":
        return render_antigravity_event(raw_line)
    return raw_line
