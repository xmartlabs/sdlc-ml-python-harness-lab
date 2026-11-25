#!/usr/bin/env python3
"""Mechanical parts of the review_pr skill. Standard library only, no network,
no git, no gh: it reads files the skill already fetched and writes JSON.

  split    <diff> <out_dir>
      One .diff per changed file under <out_dir>/files/, plus
      <out_dir>/files.json (per file: added, deleted, generated, binary) and
      <out_dir>/commentable.json (the new-side lines GitHub accepts an inline
      comment on: added and context lines inside a hunk).

  plan     <files.json> [--depth auto|quick|standard|deep] [--questions N]
           [--risk REGEX]
      Chooses how deep to review. auto picks quick for a small change on no
      risky path, deep when a changed file matches a risk pattern or the change
      is large, standard otherwise. Prints the depth, why, and how many
      scenario questions to write.

  payload  <findings.json> <commentable.json> --sha SHA --author LOGIN --me LOGIN
           [--lang es|en] [--comment-only] [-o review.json]
      Builds the one review to POST to repos/{owner}/{repo}/pulls/{n}/reviews.
      The event is decided here, not by the agent: COMMENT when any finding is
      "importante" or the PR author is the active gh user, APPROVE otherwise;
      --comment-only forces COMMENT, nothing forces APPROVE. A finding whose
      line is outside the diff goes into the body as `path:line`, so GitHub
      never rejects the review over a line.

  export   <findings.json> --sha SHA [-o review.json]
      The unattended result: the same checks as payload, written in the
      neutral shape a runner reads (verdict, summary, findings), never posted.

findings.json:
  {"body": "<review body>",
   "summary": "<one sentence, unattended runs only>",
   "findings": [{"path": "src/a.py", "line": 42, "start_line": null,
                 "severity": "importante" | "menor",
                 "worth": 1-5,
                 "quote": "<the exact code line(s) at SHA the finding rests on>",
                 "title": "<short claim>",
                 "comment": "<markdown, exactly what is posted>",
                 "suggestion": "<the concrete fix, unattended runs only>"}]}
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

# --- depth ---------------------------------------------------------------------

DEPTHS = ("quick", "standard", "deep")
#: Scenario questions to write per depth (overridable with --questions).
QUESTIONS = {"quick": 0, "standard": 8, "deep": 12}
#: Paths where a defect costs most: auth, money, schema, CI and deploy, secrets.
#: Case-insensitive; a keyword is a whole path segment or its prefix up to `_` or
#: `.`, so "author" and "tokenizer" do not count. A schema counts as a folder or
#: a schema file, not as a word in a doc's name.
RISK_DEFAULT = (
    r"(?i)(^|/)(auth|authn|authz|authentication|authorization|oauth2?|security|"
    r"permissions?|tokens?|secrets?|credentials?|payments?|billing|invoices?|"
    r"migrations?|migrate|alembic)(/|_|\.|$)|(^|/)\.github/workflows/|"
    r"(^|/)(deploy|helm|terraform|k8s)/|Dockerfile|"
    r"(^|/)schemas?(/|\.(graphql|gql|prisma|proto|sql|json|ya?ml|py|ts)$)"
)
#: quick needs both: at most this many reviewable files and changed lines.
QUICK_FILES, QUICK_LINES = 3, 80
#: deep regardless of paths past this many changed lines.
DEEP_LINES = 1500

# --- the words a review is posted in -------------------------------------------

LABELS = {
    "es": {"importante": "**Importante:**", "menor": "**Menor:**"},
    "en": {"importante": "**Important:**", "menor": "**Minor:**"},
}
OUTSIDE = {"es": "Fuera del diff:", "en": "Outside the diff:"}
WORTH_FLOOR = 4  # importante needs worth 4 or 5: a traced, visible failure.


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


# --- depth -------------------------------------------------------------------------


def plan(
    files: list[dict],
    *,
    depth: str = "auto",
    questions: int | None = None,
    risk: str = RISK_DEFAULT,
) -> dict:
    """How deep to review this change, and why. Generated and binary files do
    not count toward size; a deleted risky file still counts as risky."""
    reviewable = [f for f in files if not f.get("generated") and not f.get("binary")]
    lines = sum(f.get("added", 0) + f.get("deleted", 0) for f in reviewable)
    try:
        pattern = re.compile(risk)
    except re.error as exc:
        raise PayloadError(f"--risk is not a valid regex: {exc}") from exc
    risky = sorted({f["path"] for f in reviewable if pattern.search(f["path"])})
    if depth != "auto":
        if depth not in DEPTHS:
            raise PayloadError(f"depth must be auto or one of {DEPTHS}")
        chosen, why = depth, [f"asked for {depth}"]
    elif risky:
        chosen, why = "deep", [f"risky path: {', '.join(risky[:5])}"]
    elif lines > DEEP_LINES:
        chosen, why = "deep", [f"{lines} changed lines (> {DEEP_LINES})"]
    elif len(reviewable) <= QUICK_FILES and lines <= QUICK_LINES:
        chosen, why = "quick", [f"{len(reviewable)} files, {lines} lines"]
    else:
        chosen, why = "standard", [f"{len(reviewable)} files, {lines} lines"]
    count = QUESTIONS[chosen] if questions is None else questions
    if count < 0:
        raise PayloadError("--questions must be 0 or more")
    return {
        "depth": chosen,
        "why": why,
        "questions": count,
        "files": len(reviewable),
        "lines": lines,
        "risky": risky,
    }


def cmd_plan(args: argparse.Namespace) -> int:
    files = json.loads(Path(args.files).read_text(encoding="utf-8"))
    try:
        result = plan(files, depth=args.depth, questions=args.questions, risk=args.risk)
    except PayloadError as exc:
        print(f"plan refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


# --- findings ------------------------------------------------------------------------


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


def _check_findings(findings: list[dict], lang: str) -> None:
    """Every finding is well formed, rests on a quote, and its label, worth and
    severity agree. Raises PayloadError on the first one that does not."""
    labels = LABELS[lang]
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
        severity = f.get("severity")
        if severity not in SEVERITIES:
            raise PayloadError(f"{where}: severity must be one of {SEVERITIES}")
        comment = f.get("comment")
        if not isinstance(comment, str) or not comment.strip():
            raise PayloadError(f"{where}: comment is required")
        if not isinstance(f.get("quote"), str) or not f["quote"].strip():
            raise PayloadError(
                f"{where}: quote is required, the exact code at the PR's head "
                "the finding rests on"
            )
        worth = f.get("worth")
        if isinstance(worth, bool) or not isinstance(worth, int) or not 1 <= worth <= 5:
            raise PayloadError(f"{where}: worth must be an integer from 1 to 5")
        if severity == "importante" and worth < WORTH_FLOOR:
            raise PayloadError(
                f"{where}: importante needs worth {WORTH_FLOOR} or 5 (a traced "
                "path to a visible failure); lower it to menor or trace it"
            )
        if not comment.strip().startswith(labels[severity]):
            raise PayloadError(
                f"{where}: a {severity} comment in {lang!r} starts with "
                f"{labels[severity]!r}"
            )
        _check_text(where, comment)


def build(
    doc: dict,
    commentable: dict,
    *,
    sha: str,
    author: str,
    me: str,
    comment_only: bool = False,
    lang: str = "es",
) -> tuple[dict, dict]:
    """The review payload and a short report of how it was decided."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        raise PayloadError("--sha must be the PR's 40-character head commit")
    if lang not in LABELS:
        raise PayloadError(f"--lang must be one of {tuple(LABELS)}")
    body = (doc.get("body") or "").strip()
    findings = doc.get("findings") or []
    _check_findings(findings, lang)
    _check_text("body", body)
    inline, outside = [], []
    for f in findings:
        in_diff = _in_diff(
            commentable.get(f["path"], []), f["line"], f.get("start_line")
        )
        (inline if in_diff else outside).append(f)

    important = sum(f["severity"] == "importante" for f in findings)
    own = author.strip().lower() == me.strip().lower()
    event = "COMMENT" if important or own or comment_only else "APPROVE"

    if outside:
        body += f"\n\n{OUTSIDE[lang]}\n" + "\n".join(
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


def export(doc: dict, *, sha: str, lang: str = "es") -> dict:
    """The unattended result: checked like a payload, shaped for a runner.

    No event is chosen here (nothing is posted): ``verdict`` says what the
    interactive flow would have done, ``changes_suggested`` or ``clean``.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        raise PayloadError("--sha must be the reviewed 40-character head commit")
    if lang not in LABELS:
        raise PayloadError(f"--lang must be one of {tuple(LABELS)}")
    findings = doc.get("findings") or []
    _check_findings(findings, lang)
    for i, f in enumerate(findings):
        if not isinstance(f.get("title"), str) or not f["title"].strip():
            raise PayloadError(f"finding {i}: title is required in export")
    labels = LABELS[lang]
    summary = (doc.get("summary") or doc.get("body") or "").strip()
    if not summary:
        raise PayloadError("summary is required")
    _check_text("summary", summary)
    important = sum(f["severity"] == "importante" for f in findings)
    return {
        "schema": 1,
        "commit": sha,
        "verdict": "changes_suggested" if important else "clean",
        "summary": summary,
        "findings": [
            {
                "file": f["path"],
                "line": f["line"],
                "severity": f["severity"],
                "worth": f["worth"],
                "title": f["title"].strip(),
                "quote": f["quote"],
                # severity is its own field; drop the posting label.
                "explanation": f["comment"]
                .strip()
                .removeprefix(labels[f["severity"]])
                .strip(),
                "suggestion": (f.get("suggestion") or "").strip(),
            }
            for f in sorted(findings, key=lambda f: -f["worth"])
        ],
    }


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
            lang=args.lang,
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


def cmd_export(args: argparse.Namespace) -> int:
    doc = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    try:
        result = export(doc, sha=args.sha, lang=args.lang)
    except PayloadError as exc:
        print(f"export refused: {exc}", file=sys.stderr)
        return 2
    out = Path(args.output or Path(args.findings).with_name("review.json"))
    out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({"verdict": result["verdict"], "findings": len(result["findings"])})
    )
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
    pl = sub.add_parser("plan")
    pl.add_argument("files")
    pl.add_argument("--depth", default="auto")
    pl.add_argument("--questions", type=int)
    pl.add_argument("--risk", default=RISK_DEFAULT)
    p = sub.add_parser("payload")
    p.add_argument("findings")
    p.add_argument("commentable")
    p.add_argument("--sha", required=True)
    p.add_argument("--author", required=True)
    p.add_argument("--me", required=True)
    p.add_argument("--lang", default="es")
    p.add_argument("--comment-only", action="store_true")
    p.add_argument("-o", "--output")
    e = sub.add_parser("export")
    e.add_argument("findings")
    e.add_argument("--sha", required=True)
    e.add_argument("--lang", default="es")
    e.add_argument("-o", "--output")
    args = parser.parse_args(argv)
    if args.cmd == "split":
        return cmd_split(args.diff, args.out_dir)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd == "export":
        return cmd_export(args)
    return cmd_payload(args)


if __name__ == "__main__":
    sys.exit(main())
