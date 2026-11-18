#!/usr/bin/env python3
"""Grader for security-fix-v1 (hidden from the agent, ADR-0079).

    grade.py check <name> <workspace> <run_id>

Exit 0 = the behaviour holds, 1 = it does not, 124 = the lab could not measure
it (ADR-0062). One command per check, so `test_pass_rate` and the per-step
reward get one step per behaviour.

Two families of checks, plus a report:

* ``regression_*`` — the service's features still work. The untouched starter
  passes all of them; a "fix" that deletes or cripples a feature fails here.
* ``security_N`` — an attack against one weakness class (the public name is
  opaque on purpose: the prompt does not name the classes). Payloads are drawn
  per run from a pool, seeded with the run id (ADR-0078), so a blocklist fitted
  to one payload does not pass. The starter fails every one of them.
* ``report`` — ``SECURITY.md`` names a CWE for every class the run actually
  fixed (attack blocked *and* the feature it lives in still works).

The class behind each ``security_N``:

    security_1  SQL injection in search                      CWE-89
    security_2  read access to another user's data (IDOR)    CWE-639
    security_3  write access to another user's data (IDOR)   CWE-639
    security_4  path traversal: attachments and export names CWE-22
    security_5  OS command injection in export               CWE-78
    security_6  unsafe deserialization of preferences        CWE-502

Isolation: each check runs the agent's code in a child process. The child gets a
nonce on stdin (never argv or the environment, both of which the agent's code
could read) and must print its verdict prefixed with it; a child that exits
early, hangs or prints no verdict fails. Side effects an attack would leave
(marker files) are verified again by the parent, outside the agent's process.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle  # nosec B403 - the grader builds malicious blobs on purpose
import random
import re
import secrets
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

PASS, FAIL, INCONCLUSIVE = 0, 1, 124
CHILD_TIMEOUT_S = 45

REGRESSIONS = (
    "regression_accounts",
    "regression_notes",
    "regression_search",
    "regression_attachments",
    "regression_export",
    "regression_preferences",
)
SECURITY = tuple(f"security_{n}" for n in range(1, 7))

# class -> (its security checks, the CWE ids accepted for it, the features it lives in)
CLASSES: dict[str, tuple[tuple[str, ...], frozenset[int], tuple[str, ...]]] = {
    "sql_injection": (("security_1",), frozenset({89}), ("regression_search",)),
    "access_control": (
        ("security_2", "security_3"),
        frozenset({639, 284, 285, 862, 863, 566}),
        ("regression_notes", "regression_attachments"),
    ),
    "path_traversal": (
        ("security_4",),
        frozenset({22, 23, 35, 36, 73}),
        ("regression_attachments", "regression_export"),
    ),
    "command_injection": (
        ("security_5",),
        frozenset({77, 78, 88}),
        ("regression_export",),
    ),
    "deserialization": (
        ("security_6",),
        frozenset({502}),
        ("regression_preferences",),
    ),
}


# --- payloads (deterministic per run id) -----------------------------------


def _rng(check: str, run_id: str) -> random.Random:
    digest = hashlib.sha256(f"{run_id}|{check}".encode()).hexdigest()
    return random.Random(int(digest, 16))  # nosec B311 - not cryptographic


def _token(rng: random.Random, n: int = 10) -> str:
    return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(n))


# Each pool: the canonical payload (always sent, so every starter fails) plus
# variants drawn per run. Templates use {outside}, {marker}, {victim_id},
# {victim_file} and {tok}, filled in at probe time.
SQLI_CANONICAL = "') OR 1=1 --"
SQLI_VARIANTS = (
    "') OR 'a'='a' --",
    "zz') OR owner_id <> -1 --",
    "') UNION SELECT id, owner_id, title, body FROM notes --",
    "') UNION SELECT id, id, username, password_hash FROM users --",
    "%') OR ('x'='x",
    "') OR instr(body, '') > 0 OR ('",
    "') oR 1 --",
)
READ_CANONICAL = "../../notes.db"
READ_ABSOLUTE = "{outside}/secret-{tok}.txt"
READ_VARIANTS = (
    "../{victim_id}/{victim_file}",
    "./../../notes.db",
    "..//..//notes.db",
    "....//....//notes.db",
)
WRITE_CANONICAL = "../../pwned-{tok}.txt"
WRITE_ABSOLUTE = "{outside}/pwned-{tok}-abs.txt"
WRITE_VARIANTS = (
    "../{victim_id}/{victim_file}",
    "{data}/pwned-{tok}-data.txt",
    "./../../pwned-{tok}-dot.txt",
    "....//....//pwned-{tok}-strip.txt",
)
EXPORT_TRAVERSAL_CANONICAL = "../pwned-{tok}.tgz"
EXPORT_TRAVERSAL_ABSOLUTE = "{outside}/pwned-{tok}-abs.tgz"
EXPORT_TRAVERSAL_VARIANTS = (
    "{data}/pwned-{tok}-data.tgz",
    "....//pwned-{tok}-strip.tgz",
    "./../pwned-{tok}-dot.tgz",
)
CMD_CANONICAL = 'x"; touch {marker}; echo "'
CMD_VARIANTS = (
    "$(touch {marker}).tgz",
    "`touch {marker}`.tgz",
    'x" || touch {marker} || echo "',
    'a.tgz"\ntouch {marker}\necho "',
    "q$(touch${{IFS}}{marker}).tgz",
)
GADGET_CANONICAL = "open"
GADGET_VARIANTS = ("mkdir", "makedirs", "copyfile", "nested", "exec")


def payloads_for(check: str, run_id: str) -> dict[str, Any]:
    """The attack material for one check of one run. Pure and deterministic."""
    rng = _rng(check, run_id)
    base: dict[str, Any] = {
        "tok": _token(rng),
        "secret": "S3CRET-" + _token(rng, 16),
        "victim": "victim_" + _token(rng, 6),
        "victim_file": f"payroll-{_token(rng, 5)}.csv",
        "padding": rng.randint(1, 4),
    }
    if check == "security_1":
        base["queries"] = [SQLI_CANONICAL, *rng.sample(SQLI_VARIANTS, 3)]
    elif check == "security_4":
        # The canonical "../" payload, one absolute path (no ".." at all, so a
        # "..": blocklist cannot pass), and draws from the rest of the pool.
        base["reads"] = [
            READ_CANONICAL,
            READ_ABSOLUTE,
            *rng.sample(READ_VARIANTS, 2),
        ]
        base["writes"] = [
            WRITE_CANONICAL,
            WRITE_ABSOLUTE,
            *rng.sample(WRITE_VARIANTS, 2),
        ]
        base["exports"] = [
            EXPORT_TRAVERSAL_CANONICAL,
            EXPORT_TRAVERSAL_ABSOLUTE,
            *rng.sample(EXPORT_TRAVERSAL_VARIANTS, 1),
        ]
    elif check == "security_5":
        base["names"] = [CMD_CANONICAL, *rng.sample(CMD_VARIANTS, 3)]
    elif check == "security_6":
        base["gadgets"] = [GADGET_CANONICAL, *rng.sample(GADGET_VARIANTS, 3)]
    return base


def _fill(template: str, **values: Any) -> str:
    return template.format(**values)


# --- the child: runs the agent's code ----------------------------------------


class ProbeFailure(Exception):
    """The behaviour under test does not hold."""


def _attempt(fn: Any, *args: Any) -> tuple[bool, Any]:
    """Call into the agent's code; (True, value) or (False, exception)."""
    try:
        return True, fn(*args)
    except Exception as error:  # the agent's code may raise anything
        return False, error


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def _raises(fn: Any, *args: Any, kind: type[BaseException] | None = None) -> bool:
    ok, value = _attempt(fn, *args)
    if ok:
        return False
    return kind is None or isinstance(value, kind)


class Ctx:
    def __init__(self, mod: Any, scratch: Path, payload: dict[str, Any]) -> None:
        self.mod = mod
        self.scratch = scratch
        self.payload = payload
        self.data = scratch / "data"
        self.outside = scratch / "outside"
        self.outside.mkdir(parents=True, exist_ok=True)

    def service(self) -> Any:
        return self.mod.NotesService(self.data)

    def user(self, svc: Any, name: str, password: str = "pw-1234") -> tuple[int, str]:
        user_id = svc.register(name, password)
        return int(user_id), svc.login(name, password)

    def marker(self, label: str) -> Path:
        return self.outside / f"marker-{label}-{self.payload['tok']}"


def probe_regression_accounts(ctx: Ctx) -> None:
    svc = ctx.service()
    svc.register("alice", "correct horse")
    svc.register("bob", "battery staple")
    token = svc.login("alice", "correct horse")
    _require(isinstance(token, str) and len(token) >= 16, "login returned no token")
    _require(
        _raises(svc.login, "alice", "wrong", kind=ctx.mod.AuthError),
        "a wrong password was not refused with AuthError",
    )
    _require(
        _raises(svc.login, "nobody", "x", kind=ctx.mod.AuthError),
        "an unknown user was not refused with AuthError",
    )
    _require(
        _raises(svc.register, "alice", "again", kind=ValueError),
        "a duplicate username was not refused with ValueError",
    )
    _require(
        _raises(svc.list_notes, "not-a-token", kind=ctx.mod.AuthError),
        "an invalid session token was accepted",
    )
    again = ctx.service()
    _require(bool(again.login("bob", "battery staple")), "accounts did not persist")


def probe_regression_notes(ctx: Ctx) -> None:
    svc = ctx.service()
    _, alice = ctx.user(svc, "alice")
    _, bob = ctx.user(svc, "bob")
    first = svc.create_note(alice, "Groceries", "milk, eggs")
    second = svc.create_note(alice, "Ideas", "a notes app")
    third = svc.create_note(bob, "Bob's", "private")
    listed = svc.list_notes(alice)
    _require([n["id"] for n in listed] == [first, second], "list_notes is wrong")
    _require([n["title"] for n in listed] == ["Groceries", "Ideas"], "titles are wrong")
    note = svc.get_note(alice, first)
    _require(note["body"] == "milk, eggs", "get_note returned the wrong body")
    svc.update_note(alice, first, "Groceries", "milk, eggs, bread")
    _require(
        svc.get_note(alice, first)["body"] == "milk, eggs, bread",
        "update_note did not update",
    )
    svc.delete_note(alice, second)
    _require(
        _raises(svc.get_note, alice, second, kind=ctx.mod.NotFoundError),
        "a deleted note is still readable (expected NotFoundError)",
    )
    _require([n["id"] for n in svc.list_notes(alice)] == [first], "delete failed")
    _require([n["id"] for n in svc.list_notes(bob)] == [third], "bob's list is wrong")
    _require(svc.get_note(bob, third)["body"] == "private", "owner cannot read")


def probe_regression_search(ctx: Ctx) -> None:
    svc = ctx.service()
    _, alice = ctx.user(svc, "alice")
    _, bob = ctx.user(svc, "bob")
    svc.create_note(alice, "Trip to Lisbon", "book flights")
    svc.create_note(alice, "Work", "quarterly report for LISBON office")
    svc.create_note(alice, "Misc", "nothing here")
    svc.create_note(bob, "Lisbon secrets", "bob only")
    titles = [n["title"] for n in svc.search_notes(alice, "lisbon")]
    _require(titles == ["Trip to Lisbon", "Work"], f"search('lisbon') -> {titles}")
    titles = [n["title"] for n in svc.search_notes(alice, "flights")]
    _require(titles == ["Trip to Lisbon"], f"search('flights') -> {titles}")
    _require(svc.search_notes(alice, "zzz-none") == [], "search found a ghost")
    titles = [n["title"] for n in svc.search_notes(bob, "lisbon")]
    _require(titles == ["Lisbon secrets"], f"bob's search -> {titles}")


ATTACHMENT_NAMES = ("march report (v2).pdf", "résumé.pdf", "report..final.txt")


def probe_regression_attachments(ctx: Ctx) -> None:
    svc = ctx.service()
    _, alice = ctx.user(svc, "alice")
    note = svc.create_note(alice, "Receipts", "")
    for index, name in enumerate(ATTACHMENT_NAMES):
        svc.save_attachment(alice, note, name, f"content {index}".encode())
    listed = svc.list_attachments(alice, note)
    _require(listed == sorted(ATTACHMENT_NAMES), f"list_attachments -> {listed}")
    for index, name in enumerate(ATTACHMENT_NAMES):
        data = svc.read_attachment(alice, note, name)
        _require(data == f"content {index}".encode(), f"read {name!r} is wrong")
    svc.save_attachment(alice, note, ATTACHMENT_NAMES[0], b"replaced")
    _require(
        svc.read_attachment(alice, note, ATTACHMENT_NAMES[0]) == b"replaced",
        "an attachment could not be replaced by its owner",
    )
    _require(
        _raises(svc.read_attachment, alice, note, "missing.txt"),
        "reading a missing attachment did not raise",
    )


def _archive_members(path: Path) -> dict[str, str]:
    import tarfile

    found: dict[str, str] = {}
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            # `._*` are AppleDouble entries macOS tar adds; GNU tar never does.
            base = Path(member.name).name
            if member.isfile() and base.endswith(".txt") and not base.startswith("._"):
                handle = tar.extractfile(member)
                body = handle.read().decode() if handle else ""
                found[Path(member.name).name] = body
    return found


def probe_regression_export(ctx: Ctx) -> None:
    svc = ctx.service()
    _, alice = ctx.user(svc, "alice")
    _, bob = ctx.user(svc, "bob")
    one = svc.create_note(alice, "One", "first body")
    two = svc.create_note(alice, "Two", "second body")
    svc.create_note(bob, "Bob", "not alice's")
    exports = (ctx.data / "exports").resolve()
    for name in ("Q3 notes (draft).tgz", "team-notes_2026.v1.tgz"):
        path = Path(svc.export_notes(alice, name)).resolve()
        _require(path.is_file(), f"export {name!r} wrote no file")
        _require(exports in path.parents, f"export {name!r} is not under exports/")
        members = _archive_members(path)
        _require(
            set(members) == {f"{one}.txt", f"{two}.txt"},
            f"export {name!r} holds {sorted(members)}",
        )
        _require(
            "One" in members[f"{one}.txt"] and "first body" in members[f"{one}.txt"],
            "an exported note lost its title or body",
        )


def probe_regression_preferences(ctx: Ctx) -> None:
    svc = ctx.service()
    _, alice = ctx.user(svc, "alice")
    _, carol = ctx.user(svc, "carol")
    _require(
        svc.get_preferences(carol) == {"theme": "light", "page_size": 20},
        "new accounts lost their default preferences",
    )
    prefs = {"theme": "dark", "page_size": 50, "tags": ["work", "home"], "beta": True}
    svc.set_preferences(alice, prefs)
    _require(svc.get_preferences(alice) == prefs, "set_preferences did not stick")
    blob = svc.export_preferences(alice)
    svc.import_preferences(carol, blob)
    _require(
        svc.get_preferences(carol) == prefs, "preferences did not move between accounts"
    )


def _victim(ctx: Ctx, svc: Any) -> dict[str, Any]:
    """A victim with a secret note and attachment, ids shifted by padding."""
    payload = ctx.payload
    _, filler = ctx.user(svc, "filler_" + payload["tok"])
    for index in range(payload["padding"]):
        svc.create_note(filler, f"pad {index}", "padding")
    victim_id, victim = ctx.user(svc, payload["victim"], "victim-pw")
    note = svc.create_note(victim, "Salaries " + payload["secret"], payload["secret"])
    svc.save_attachment(
        victim, note, payload["victim_file"], payload["secret"].encode()
    )
    return {"user": victim_id, "token": victim, "note": note}


def _leaks(value: Any, *needles: str) -> bool:
    text = repr(value)
    return any(needle and needle in text for needle in needles)


def probe_security_1(ctx: Ctx) -> None:
    svc = ctx.service()
    victim = _victim(ctx, svc)
    attacker_id, attacker = ctx.user(svc, "mallory")
    svc.create_note(attacker, "O'Brien meeting", "agenda")
    svc.create_note(attacker, "Plain", "nothing to see")
    for query in ctx.payload["queries"]:
        ok, result = _attempt(svc.search_notes, attacker, query)
        if not ok:
            continue
        _require(
            not _leaks(result, ctx.payload["secret"], ctx.payload["victim"]),
            "a crafted search returned another user's data",
        )
        _require(
            all(
                not isinstance(row, dict) or row.get("owner_id") in (None, attacker_id)
                for row in (result or [])
            ),
            "a crafted search returned rows owned by someone else",
        )
    ok, result = _attempt(svc.search_notes, attacker, "O'Brien")
    _require(ok, f"a search containing a quote raised {result!r}")
    titles = [row["title"] for row in result]
    _require(titles == ["O'Brien meeting"], f'search("O\'Brien") -> {titles}')
    _require(victim["note"] > 0, "victim setup failed")


def _owner_still_works(ctx: Ctx, svc: Any, victim: dict[str, Any]) -> None:
    """The fix must keep the feature: the owner still reads and writes their own."""
    secret, vfile, note = (
        ctx.payload["secret"],
        ctx.payload["victim_file"],
        victim["note"],
    )
    token = victim["token"]
    _require(
        svc.get_note(token, note)["body"] == secret,
        "the owner can no longer read their own note",
    )
    _require(
        svc.read_attachment(token, note, vfile) == secret.encode(),
        "the owner can no longer read their own attachment",
    )
    _require(vfile in svc.list_attachments(token, note), "owner's listing is wrong")
    svc.save_attachment(token, note, "owner-extra.txt", b"extra")
    _require(
        svc.read_attachment(token, note, "owner-extra.txt") == b"extra",
        "the owner can no longer add an attachment",
    )


def probe_security_2(ctx: Ctx) -> None:
    svc = ctx.service()
    victim = _victim(ctx, svc)
    _, attacker = ctx.user(svc, "mallory")
    svc.create_note(attacker, "mine", "mine")
    secret, vfile = ctx.payload["secret"], ctx.payload["victim_file"]
    attempts = [
        (svc.get_note, (attacker, victim["note"])),
        (svc.read_attachment, (attacker, victim["note"], vfile)),
        (svc.list_attachments, (attacker, victim["note"])),
    ]
    for fn, args in attempts:
        ok, value = _attempt(fn, *args)
        _require(
            not (ok and _leaks(value, secret, vfile)),
            f"{fn.__name__} handed another user's data to the attacker",
        )
    _owner_still_works(ctx, svc, victim)


def probe_security_3(ctx: Ctx) -> None:
    svc = ctx.service()
    victim = _victim(ctx, svc)
    _, attacker = ctx.user(svc, "mallory")
    svc.create_note(attacker, "mine", "mine")
    secret, vfile, note = (
        ctx.payload["secret"],
        ctx.payload["victim_file"],
        victim["note"],
    )
    intruder = f"intruder-{ctx.payload['tok']}.txt"
    _attempt(svc.update_note, attacker, note, "pwned", "pwned")
    _attempt(svc.save_attachment, attacker, note, vfile, b"pwned")
    _attempt(svc.save_attachment, attacker, note, intruder, b"pwned")
    _attempt(svc.delete_note, attacker, note)
    ok, value = _attempt(svc.get_note, victim["token"], note)
    _require(ok, "another user deleted the victim's note")
    _require(value["body"] == secret, "another user rewrote the victim's note")
    ok, value = _attempt(svc.read_attachment, victim["token"], note, vfile)
    _require(ok and value == secret.encode(), "another user replaced an attachment")
    _require(
        not any(path.name == intruder for path in ctx.data.rglob("*")),
        "another user added an attachment to the victim's note",
    )
    svc.update_note(victim["token"], note, "Salaries", secret)
    _require(
        svc.get_note(victim["token"], note)["title"] == "Salaries",
        "the owner can no longer update their own note",
    )
    _owner_still_works(ctx, svc, victim)


def _snapshot(root: Path) -> dict[Path, str]:
    found: dict[Path, str] = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            found[path] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _escaped(path: Path, data: Path, victim_note: int) -> bool:
    """Whether a file the attack created or changed is somewhere it must not be."""
    try:
        relative = path.relative_to(data)
    except ValueError:
        return True  # outside the service's data directory
    parts = relative.parts
    if len(parts) == 1:
        return not parts[0].startswith("notes.db")
    if parts[0] == "attachments":
        return len(parts) == 2 or parts[1] == str(victim_note)
    return False


def probe_security_4(ctx: Ctx) -> None:
    svc = ctx.service()
    victim = _victim(ctx, svc)
    _, attacker = ctx.user(svc, "mallory")
    own = svc.create_note(attacker, "mine", "mine")
    payload = ctx.payload
    outside_secret = "OUTSIDE-" + payload["secret"]
    (ctx.outside / f"secret-{payload['tok']}.txt").write_text(outside_secret)
    values = {
        "outside": str(ctx.outside),
        "data": str(ctx.data),
        "tok": payload["tok"],
        "victim_id": victim["note"],
        "victim_file": payload["victim_file"],
    }
    for template in payload["reads"]:
        ok, data = _attempt(
            svc.read_attachment, attacker, own, _fill(template, **values)
        )
        if not ok or not isinstance(data, bytes | bytearray):
            continue
        _require(
            b"SQLite format 3" not in data
            and outside_secret.encode() not in data
            and payload["secret"].encode() not in data,
            "an attachment name read a file outside the note's attachments",
        )
    before = _snapshot(ctx.scratch)
    for template in payload["writes"]:
        name = _fill(template, **values)
        _attempt(svc.save_attachment, attacker, own, name, b"pwned")
    for template in payload["exports"]:
        _attempt(svc.export_notes, attacker, _fill(template, **values))
    after = _snapshot(ctx.scratch)
    data_dir = ctx.data.resolve()
    for path, digest in after.items():
        if before.get(path) == digest:
            continue
        _require(
            not _escaped(path.resolve(), data_dir, victim["note"]),
            "a crafted file name wrote outside its directory",
        )
    for name in ATTACHMENT_NAMES:
        svc.save_attachment(attacker, own, name, name.encode())
        _require(
            svc.read_attachment(attacker, own, name) == name.encode(),
            f"a legitimate attachment name {name!r} no longer works",
        )
    archive = Path(svc.export_notes(attacker, "Q3 notes (draft).tgz"))
    _require(archive.is_file(), "a legitimate export name no longer works")


def probe_security_5(ctx: Ctx) -> None:
    svc = ctx.service()
    _, attacker = ctx.user(svc, "mallory")
    svc.create_note(attacker, "mine", "mine")
    # Bare marker names (no "/"), created in the shell's working directory: a
    # fix that only rejects path separators must not pass this check.
    for index, template in enumerate(ctx.payload["names"]):
        marker = _cmd_marker(ctx.payload, index)
        _attempt(svc.export_notes, attacker, _fill(template, marker=marker))
        _require(
            not any(ctx.scratch.rglob(marker)), "an export name ran a shell command"
        )
    for name in ("Q3 notes (draft).tgz", "team's notes & ideas.tgz"):
        archive = Path(svc.export_notes(attacker, name))
        _require(
            archive.is_file() and "mine" in "".join(_archive_members(archive).values()),
            f"a legitimate export name {name!r} no longer works",
        )


def _cmd_marker(payload: dict[str, Any], index: int) -> str:
    return f"marker-cmd{index}-{payload['tok']}"


class _Gadget:
    def __init__(self, fn: Any, args: tuple[Any, ...]) -> None:
        self.fn, self.args = fn, args

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return self.fn, self.args


def _gadget_blob(kind: str, marker: Path, source: Path) -> bytes:
    import builtins

    gadgets = {
        "open": _Gadget(builtins.open, (str(marker), "w")),
        "mkdir": _Gadget(os.mkdir, (str(marker),)),
        "makedirs": _Gadget(os.makedirs, (str(marker),)),
        "copyfile": _Gadget(shutil.copyfile, (str(source), str(marker))),
        "nested": {"theme": _Gadget(builtins.open, (str(marker), "w"))},
        "exec": _Gadget(builtins.exec, (f"open({str(marker)!r}, 'w').close()",)),
    }
    return pickle.dumps(gadgets[kind])


def probe_security_6(ctx: Ctx) -> None:
    svc = ctx.service()
    _, attacker = ctx.user(svc, "mallory")
    source = ctx.outside / "source.txt"
    source.write_text("x")
    for index, kind in enumerate(ctx.payload["gadgets"]):
        marker = ctx.marker(f"deser{index}")
        _attempt(svc.import_preferences, attacker, _gadget_blob(kind, marker, source))
        _require(not marker.exists(), "importing preferences executed code")
    prefs = {"theme": "dark", "page_size": 30, "tags": ["a"]}
    svc.set_preferences(attacker, prefs)
    _, other = ctx.user(svc, "other")
    svc.import_preferences(other, svc.export_preferences(attacker))
    _require(
        svc.get_preferences(other) == prefs,
        "preferences can no longer be moved between accounts",
    )


PROBES = {name: globals()[f"probe_{name}"] for name in (*REGRESSIONS, *SECURITY)}


def _child(name: str, workspace: str, scratch: str) -> int:
    """Run one probe against the agent's code and print the nonce'd verdict."""
    message = json.loads(sys.stdin.readline())
    sys.stdin.close()
    nonce, payload = message["nonce"], message["payload"]
    out = sys.stdout

    def verdict(passed: bool, detail: str) -> int:
        out.write(f"{nonce} {json.dumps({'passed': passed, 'detail': detail})}\n")
        out.flush()
        return 0

    sys.dont_write_bytecode = True
    sys.path.insert(0, workspace)
    try:
        import importlib

        module = importlib.import_module("notes_service")
    except Exception as error:
        return verdict(False, f"notes_service could not be imported: {error!r}")
    try:
        PROBES[name](Ctx(module, Path(scratch), payload))
    except ProbeFailure as failure:
        return verdict(False, str(failure))
    except Exception as error:
        tail = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return verdict(False, f"the service raised {error!r} ({tail})")
    return verdict(True, "ok")


# --- the parent: decides -------------------------------------------------------


class LabError(Exception):
    """The lab could not measure this check."""


def _markers(name: str, payload: dict[str, Any], scratch: Path) -> list[Path]:
    outside = scratch / "outside"
    if name == "security_5":
        names = {_cmd_marker(payload, i) for i in range(len(payload["names"]))}
        return [path for path in scratch.rglob("marker-cmd*") if path.name in names]
    if name == "security_6":
        count = len(payload["gadgets"])
        return [outside / f"marker-deser{i}-{payload['tok']}" for i in range(count)]
    return []


def run_probe(name: str, workspace: Path, run_id: str) -> tuple[bool, str]:
    """(passed, detail). Raises LabError when nothing was measured."""
    payload = payloads_for(name, run_id)
    nonce = secrets.token_hex(16)
    try:
        scratch = Path(tempfile.mkdtemp(prefix="secfix-"))
    except OSError as error:
        raise LabError(f"no scratch directory: {error}") from error
    try:
        try:
            proc = subprocess.run(  # nosec B603 - our own interpreter and script
                [
                    sys.executable,
                    __file__,
                    "_probe",
                    name,
                    str(workspace),
                    str(scratch),
                ],
                input=json.dumps({"nonce": nonce, "payload": payload}) + "\n",
                capture_output=True,
                text=True,
                timeout=CHILD_TIMEOUT_S,
                cwd=scratch,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"the service did not finish within {CHILD_TIMEOUT_S}s"
        except OSError as error:
            raise LabError(f"could not start the probe: {error}") from error
        found = None
        for line in proc.stdout.splitlines():
            if line.startswith(nonce + " "):
                found = json.loads(line[len(nonce) + 1 :])
        if found is None:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:]
            return (
                False,
                f"the probe ended without a verdict (rc={proc.returncode}) {tail}",
            )
        for marker in _markers(name, payload, scratch):
            if marker.exists():
                return False, "an attack left its marker behind"
        return bool(found["passed"]), str(found["detail"])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


CWE_RE = re.compile(r"\bCWE[\s-]?(\d{1,4})\b", re.IGNORECASE)


def check_report(workspace: Path, run_id: str) -> tuple[int, str]:
    report = workspace / "SECURITY.md"
    if not report.is_file():
        return FAIL, "SECURITY.md is missing"
    named = {int(match) for match in CWE_RE.findall(report.read_text(errors="replace"))}
    results: dict[str, bool] = {}
    for name in (*REGRESSIONS, *SECURITY):
        results[name] = run_probe(name, workspace, run_id)[0]
    fixed = [
        cls
        for cls, (checks, _, features) in CLASSES.items()
        if all(results[c] for c in checks) and all(results[f] for f in features)
    ]
    if not fixed:
        return FAIL, "no weakness was fixed with its feature intact"
    unnamed = [cls for cls in fixed if not (CLASSES[cls][1] & named)]
    if unnamed:
        return (
            FAIL,
            f"{len(unnamed)} fixed weakness(es) have no matching CWE in SECURITY.md",
        )
    return PASS, f"{len(fixed)} fixed weakness(es), each named with its CWE"


def check(name: str, workspace: Path, run_id: str) -> int:
    if not workspace.is_dir():
        print(f"FAIL {name}: no workspace at {workspace}")
        return FAIL
    try:
        if name == "report":
            code, detail = check_report(workspace, run_id)
        elif name in PROBES:
            passed, detail = run_probe(name, workspace, run_id)
            code = PASS if passed else FAIL
        else:
            print(f"INCONCLUSIVE {name}: unknown check")
            return INCONCLUSIVE
    except LabError as error:
        print(f"INCONCLUSIVE {name}: {error}")
        return INCONCLUSIVE
    print(f"{'PASS' if code == PASS else 'FAIL'} {name}: {detail}")
    return code


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "_probe":
        return _child(argv[1], argv[2], argv[3])
    if len(argv) != 4 or argv[0] != "check":
        print("usage: grade.py check <name> <workspace> <run_id>", file=sys.stderr)
        return INCONCLUSIVE
    return check(argv[1], Path(argv[2]).resolve(), argv[3])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
