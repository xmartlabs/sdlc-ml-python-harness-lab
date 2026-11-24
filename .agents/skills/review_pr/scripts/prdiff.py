#!/usr/bin/env python3
"""Mechanical parts of the review_pr skill. Standard library only, no network,
no git, no gh: it reads files the skill already fetched and writes JSON.

  split    <diff> <out_dir>
      One .diff per changed file under <out_dir>/files/, plus
      <out_dir>/files.json (per file: added, deleted, generated, binary) and
      <out_dir>/commentable.json (the new-side lines GitHub accepts an inline
      comment on: added and context lines inside a hunk).

  payload  <findings.json> <commentable.json> --sha SHA --author LOGIN --me LOGIN
           [--comment-only] [-o review.json]
      Builds the one review to POST to repos/{owner}/{repo}/pulls/{n}/reviews.
      The event is decided here, not by the agent: COMMENT when any finding is
      "importante" or the PR author is the active gh user, APPROVE otherwise;
      --comment-only forces COMMENT, nothing forces APPROVE. A finding whose
      line is outside the diff goes into the body as `path:line`, so GitHub
      never rejects the review over a line.

findings.json:
  {"body": "<review body>",
   "findings": [{"path": "src/a.py", "line": 42, "start_line": null,
                 "severity": "importante" | "menor", "comment": "<markdown>"}]}
"""

# A CLI the skill runs: printing its report is its job.
# ruff: noqa: T201

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")
# The new side when git C-quoted the paths: diff --git "a/x" "b/x".
HEADER_QUOTED = re.compile(r'"b/(?:[^"\\]|\\.)*"$')
GENERATED = re.compile(
    r"(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|"
    r"uv\.lock|poetry\.lock|Pipfile\.lock|Cargo\.lock|go\.sum|composer\.lock|"
    r"Gemfile\.lock)$|\.min\.(js|css)$|(^|/)(__generated__|generated)/"
)
SEVERITIES = ("importante", "menor")
# Prose punctuation the skill's comments never use (em dash, double hyphen).
BANNED = ("—", " -- ")


def _diff_path(raw: str, prefix: str) -> str | None:
    """A path from a ``---``/``+++`` line: None for /dev/null, never a tab.

    git ends the line with a tab when the path has a space, and C-quotes a
    path with non-ASCII or special characters (``"b/caf\\303\\251.py"``).
    Either left as is would never equal the path a finding names.
    """
    raw = raw.split("\t", 1)[0]
    if raw == "/dev/null":
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        raw = _unquote(raw[1:-1])
    return raw.removeprefix(prefix)


def _unquote(text: str) -> str:
    """Undo git's C-style path quoting (octal bytes and backslash escapes)."""
    simple = {
        "n": 10,
        "t": 9,
        '"': 34,
        "\\": 92,
        "a": 7,
        "b": 8,
        "f": 12,
        "r": 13,
        "v": 11,
    }
    out = bytearray()
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in "01234567" and re.fullmatch(r"[0-7]{3}", text[i + 1 : i + 4]):
                out.append(int(text[i + 1 : i + 4], 8))
                i += 4
                continue
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
        out.extend(char.encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


class PayloadError(ValueError):
    """findings.json cannot become a review as written."""


def _ranges(lines: set[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for n in sorted(lines):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


def parse(diff_text: str) -> list[dict]:
    """Each file of a unified diff: its path, text, counts and commentable lines.

    Hunks are walked by their own line counts, so a removed line whose text
    starts with "-- " or an added one starting with "++ " is never read as a
    file header.
    """
    files = []
    for chunk in re.split(r"(?m)^(?=diff --git )", diff_text):
        if not chunk.startswith("diff --git "):
            continue
        lines = chunk.splitlines()
        header = HEADER.match(lines[0])
        quoted = HEADER_QUOTED.search(lines[0])
        if header:
            old_path = new_path = header.group(2)
        elif quoted:
            old_path = new_path = _diff_path(quoted.group(0), "b/")
        else:
            old_path = new_path = None
        commentable: set[int] = set()
        added = deleted = 0
        binary = False
        old_left = new_left = 0
        new_line = 0
        for line in lines[1:]:
            if old_left > 0 or new_left > 0:
                if line.startswith("+"):
                    commentable.add(new_line)
                    new_line += 1
                    new_left -= 1
                    added += 1
                elif line.startswith("-"):
                    old_left -= 1
                    deleted += 1
                elif line.startswith("\\"):
                    continue  # "\ No newline at end of file"
                else:  # context: " text", or "" when trailing spaces were stripped
                    commentable.add(new_line)
                    new_line += 1
                    old_left -= 1
                    new_left -= 1
                continue
            hunk = HUNK.match(line)
            if hunk:
                old_left = int(hunk.group(1) or 1)
                new_line = int(hunk.group(2))
                new_left = int(hunk.group(3) or 1)
            elif line.startswith("--- "):
                old_path = _diff_path(line[4:], "a/")
            elif line.startswith("+++ "):
                new_path = _diff_path(line[4:], "b/")
            elif line.startswith("Binary files ") or line == "GIT binary patch":
                binary = True
        path = new_path or old_path or "unknown"
        files.append(
            {
                "path": path,
                "deleted_file": new_path is None,
                "added": added,
                "deleted": deleted,
                "binary": binary,
                "generated": bool(GENERATED.search(path)),
                "commentable": _ranges(commentable) if new_path else [],
                "text": chunk,
            }
        )
    return files


def cmd_split(diff: Path, out_dir: Path) -> int:
    files = parse(diff.read_text(encoding="utf-8", errors="replace"))
    (out_dir / "files").mkdir(parents=True, exist_ok=True)
    index, commentable = [], {}
    for f in files:
        name = f["path"].replace("/", "__") + ".diff"
        (out_dir / "files" / name).write_text(f["text"], encoding="utf-8")
        index.append(
            {k: v for k, v in f.items() if k not in ("text", "commentable")}
            | {"file": f"files/{name}"}
        )
        if f["commentable"]:
            commentable[f["path"]] = f["commentable"]
    (out_dir / "files.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "commentable.json").write_text(
        json.dumps(commentable, indent=2) + "\n", encoding="utf-8"
    )
    for f in index:
        flags = ",".join(k for k in ("generated", "binary", "deleted_file") if f[k])
        print(
            f"{f['added']:>6} +{f['deleted']:>6} -  {f['path']}"
            + (f"  [{flags}]" if flags else "")
        )
    print(f"{len(index)} files -> {out_dir}")
    return 0


def _in_diff(ranges: list[list[int]], line: int, start: int | None) -> bool:
    for low, high in ranges:
        if low <= line <= high:
            return start is None or low <= start <= line
    return False


# Fenced blocks and inline code: a command such as `git checkout -- f` is code,
# not prose punctuation.
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


def _check_text(where: str, text: str) -> None:
    prose = CODE.sub(" ", text)
    for mark in BANNED:
        if mark in prose:
            raise PayloadError(
                f"{where}: rewrite without {mark.strip()!r} as punctuation"
            )


def build(
    doc: dict,
    commentable: dict,
    *,
    sha: str,
    author: str,
    me: str,
    comment_only: bool = False,
) -> tuple[dict, dict]:
    """The review payload and a short report of how it was decided."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        raise PayloadError("--sha must be the PR's 40-character head commit")
    body = (doc.get("body") or "").strip()
    findings = doc.get("findings") or []
    inline, outside = [], []
    for i, f in enumerate(findings, 1):
        where = f"finding {i}"
        if not isinstance(f.get("path"), str) or not f["path"]:
            raise PayloadError(f"{where}: path is required")
        if not isinstance(f.get("line"), int) or f["line"] < 1:
            raise PayloadError(f"{where}: line must be a positive integer")
        start = f.get("start_line")
        if start is not None and (
            not isinstance(start, int) or not 1 <= start < f["line"]
        ):
            raise PayloadError(f"{where}: start_line must be below line")
        if f.get("severity") not in SEVERITIES:
            raise PayloadError(f"{where}: severity must be one of {SEVERITIES}")
        if not isinstance(f.get("comment"), str) or not f["comment"].strip():
            raise PayloadError(f"{where}: comment is required")
        _check_text(where, f["comment"])
        in_diff = _in_diff(commentable.get(f["path"], []), f["line"], start)
        (inline if in_diff else outside).append(f)
    _check_text("body", body)

    important = sum(f["severity"] == "importante" for f in findings)
    own = author.strip().lower() == me.strip().lower()
    event = "COMMENT" if important or own or comment_only else "APPROVE"

    if outside:
        body += "\n\nFuera del diff:\n" + "\n".join(
            f"- `{f['path']}:{f['line']}`: {f['comment'].strip()}" for f in outside
        )
    body = body.strip()
    if not body:
        raise PayloadError("body is required")

    comments = []
    for f in inline:
        c = {
            "path": f["path"],
            "line": f["line"],
            "side": "RIGHT",
            "body": f["comment"].strip(),
        }
        if f.get("start_line") is not None:
            c |= {"start_line": f["start_line"], "start_side": "RIGHT"}
        comments.append(c)
    payload = {"commit_id": sha, "event": event, "body": body, "comments": comments}
    reasons = (
        (["importante"] if important else [])
        + (["own PR"] if own else [])
        + (["--comment-only"] if comment_only else [])
    )
    report = {
        "event": event,
        "why": reasons or ["no importante finding"],
        "inline": len(comments),
        "in_body": len(outside),
        "importante": important,
        "menor": len(findings) - important,
    }
    return payload, report


def cmd_payload(args: argparse.Namespace) -> int:
    doc = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    commentable = json.loads(Path(args.commentable).read_text(encoding="utf-8"))
    try:
        payload, report = build(
            doc,
            commentable,
            sha=args.sha,
            author=args.author,
            me=args.me,
            comment_only=args.comment_only,
        )
    except PayloadError as exc:
        print(f"payload refused: {exc}", file=sys.stderr)
        return 2
    out = Path(args.output or Path(args.findings).with_name("review.json"))
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    print(f"review -> {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split")
    s.add_argument("diff", type=Path)
    s.add_argument("out_dir", type=Path)
    p = sub.add_parser("payload")
    p.add_argument("findings")
    p.add_argument("commentable")
    p.add_argument("--sha", required=True)
    p.add_argument("--author", required=True)
    p.add_argument("--me", required=True)
    p.add_argument("--comment-only", action="store_true")
    p.add_argument("-o", "--output")
    args = parser.parse_args(argv)
    if args.cmd == "split":
        return cmd_split(args.diff, args.out_dir)
    return cmd_payload(args)


if __name__ == "__main__":
    sys.exit(main())
