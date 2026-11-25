"""prdiff.py: diff parsing, commentable lines and the review's event rules."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "prdiff", Path(__file__).resolve().parents[1] / "scripts" / "prdiff.py"
)
prdiff = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(prdiff)

SHA = "a" * 40

DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,4 +10,5 @@ def f():
 keep = 1
--- removed line that looks like a header
+++ added line that looks like a header
+second added
 tail = 2
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+one
+two
diff --git a/gone.txt b/gone.txt
deleted file mode 100644
--- a/gone.txt
+++ /dev/null
@@ -1 +0,0 @@
-bye
diff --git a/frontend/package-lock.json b/frontend/package-lock.json
--- a/frontend/package-lock.json
+++ b/frontend/package-lock.json
@@ -1 +1 @@
-{}
+{"a": 1}
"""


class ParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.files = {f["path"]: f for f in prdiff.parse(DIFF)}

    def test_header_lookalikes_inside_a_hunk_are_content(self) -> None:
        app = self.files["src/app.py"]
        self.assertEqual((app["added"], app["deleted"]), (2, 1))
        # keep=10, added 11 and 12, tail=13: the removed line takes no new number.
        self.assertEqual(app["commentable"], [[10, 13]])

    def test_new_deleted_and_generated_files(self) -> None:
        self.assertEqual(self.files["new.txt"]["commentable"], [[1, 2]])
        self.assertTrue(self.files["gone.txt"]["deleted_file"])
        self.assertEqual(self.files["gone.txt"]["commentable"], [])
        self.assertTrue(self.files["frontend/package-lock.json"]["generated"])
        self.assertFalse(self.files["src/app.py"]["generated"])

    def test_paths_with_spaces_or_non_ascii_match_the_finding(self) -> None:
        # As git writes them: a trailing tab after a path with a space, and
        # C-quoted octal bytes for a non-ASCII one.
        diff = (
            "diff --git a/docs/my file.md b/docs/my file.md\n"
            "--- a/docs/my file.md\t\n"
            "+++ b/docs/my file.md\t\n"
            "@@ -1 +1 @@\n-a\n+b\n"
            'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
            '--- "a/caf\\303\\251.py"\n'
            '+++ "b/caf\\303\\251.py"\n'
            "@@ -1 +1 @@\n-a\n+b\n"
            'diff --git "a/\\303\\261.png" "b/\\303\\261.png"\n'
            "Binary files differ\n"
        )
        paths = [f["path"] for f in prdiff.parse(diff)]
        self.assertEqual(paths, ["docs/my file.md", "caf\u00e9.py", "\u00f1.png"])

    def test_split_writes_one_file_per_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "pr.diff").write_text(DIFF, encoding="utf-8")
            prdiff.cmd_split(out / "pr.diff", out / "split")
            commentable = json.loads((out / "split" / "commentable.json").read_text())
            self.assertEqual(commentable["src/app.py"], [[10, 13]])
            self.assertTrue((out / "split" / "files" / "src__app.py.diff").is_file())


def _finding(severity: str = "menor", line: int = 11, path: str = "src/app.py") -> dict:
    return {
        "path": path,
        "line": line,
        "severity": severity,
        "comment": "**Menor:** algo.",
    }


COMMENTABLE = {"src/app.py": [[10, 13]]}


class PayloadTests(unittest.TestCase):
    def build(self, findings: list[dict], **kw) -> tuple[dict, dict]:
        args = {"sha": SHA, "author": "someone", "me": "reviewer"} | kw
        return prdiff.build(
            {"body": "Revisé el cambio.", "findings": findings}, COMMENTABLE, **args
        )

    def test_no_findings_or_only_minor_approves(self) -> None:
        self.assertEqual(self.build([])[0]["event"], "APPROVE")
        self.assertEqual(self.build([_finding()])[0]["event"], "APPROVE")

    def test_an_important_finding_never_approves(self) -> None:
        payload, report = self.build([_finding(), _finding("importante")])
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(report["importante"], 1)

    def test_own_pr_and_comment_only_never_approve(self) -> None:
        self.assertEqual(self.build([], author="Reviewer")[0]["event"], "COMMENT")
        self.assertEqual(self.build([], comment_only=True)[0]["event"], "COMMENT")

    def test_inline_comment_shape(self) -> None:
        payload, _ = self.build([_finding() | {"start_line": 10}])
        self.assertEqual(payload["commit_id"], SHA)
        self.assertEqual(
            payload["comments"],
            [
                {
                    "path": "src/app.py",
                    "line": 11,
                    "side": "RIGHT",
                    "body": "**Menor:** algo.",
                    "start_line": 10,
                    "start_side": "RIGHT",
                }
            ],
        )

    def test_a_line_outside_the_diff_goes_to_the_body(self) -> None:
        payload, report = self.build([_finding(line=99), _finding(path="other.py")])
        self.assertEqual(payload["comments"], [])
        self.assertEqual(report["in_body"], 2)
        self.assertIn("`src/app.py:99`", payload["body"])

    def test_malformed_findings_and_banned_punctuation_are_refused(self) -> None:
        for bad in (
            _finding("blocker"),
            _finding(line=0),
            _finding() | {"comment": " "},
            _finding() | {"comment": "falla — arreglalo"},
            _finding() | {"start_line": 12},
        ):
            with self.subTest(bad=bad), self.assertRaises(prdiff.PayloadError):
                self.build([bad])
        with self.assertRaises(prdiff.PayloadError):
            self.build([], sha="abc")

    def test_a_double_hyphen_inside_code_is_not_punctuation(self) -> None:
        for text in (
            "**Menor:** usá `git checkout -- src/a.py`.",
            "**Menor:** así:\n```bash\npytest -- -k x\n```",
        ):
            with self.subTest(text=text):
                self.build([_finding() | {"comment": text}])
        with self.assertRaises(prdiff.PayloadError):
            self.build([_finding() | {"comment": "falla -- arreglalo `x`"}])


if __name__ == "__main__":
    unittest.main()
