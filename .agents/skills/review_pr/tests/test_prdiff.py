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
        "worth": 5 if severity == "importante" else 3,
        "quote": "second added",
        "comment": (
            "**Importante:** algo." if severity == "importante" else "**Menor:** algo."
        ),
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


class QualityGateTests(unittest.TestCase):
    """What a finding must carry before it can be posted or exported."""

    def build(self, findings: list[dict], **kw) -> tuple[dict, dict]:
        args = {"sha": SHA, "author": "someone", "me": "reviewer"} | kw
        return prdiff.build(
            {"body": "Body.", "findings": findings}, COMMENTABLE, **args
        )

    def test_a_finding_without_a_quote_is_refused(self) -> None:
        for quote in (None, "", "   "):
            with self.subTest(quote=quote), self.assertRaises(prdiff.PayloadError):
                self.build([_finding() | {"quote": quote}])

    def test_worth_must_be_1_to_5(self) -> None:
        for worth in (None, 0, 6, "5", True, 4.0):
            with self.subTest(worth=worth), self.assertRaises(prdiff.PayloadError):
                self.build([_finding() | {"worth": worth}])

    def test_importante_needs_a_traced_worth(self) -> None:
        with self.assertRaises(prdiff.PayloadError):
            self.build([_finding("importante") | {"worth": 3}])
        self.build([_finding("importante") | {"worth": 4}])
        # A low worth is fine on a minor finding.
        self.build([_finding() | {"worth": 1}])

    def test_the_label_matches_severity_and_language(self) -> None:
        with self.assertRaises(prdiff.PayloadError):
            self.build([_finding() | {"comment": "**Importante:** algo."}])
        with self.assertRaises(prdiff.PayloadError):
            self.build([_finding()], lang="en")
        english = _finding() | {"comment": "**Minor:** something."}
        payload, _ = self.build([english], lang="en")
        self.assertEqual(payload["comments"][0]["body"], "**Minor:** something.")
        with self.assertRaises(prdiff.PayloadError):
            self.build([english], lang="fr")

    def test_outside_the_diff_header_follows_the_language(self) -> None:
        english = _finding(line=99) | {"comment": "**Minor:** something."}
        payload, _ = self.build([english], lang="en")
        self.assertIn("Outside the diff:", payload["body"])
        payload, _ = self.build([_finding(line=99)])
        self.assertIn("Fuera del diff:", payload["body"])


def _files(*entries: tuple[str, int]) -> list[dict]:
    return [
        {"path": p, "added": n, "deleted": 0, "generated": False, "binary": False}
        for p, n in entries
    ]


class PlanTests(unittest.TestCase):
    def test_small_and_safe_is_quick_with_no_questions(self) -> None:
        result = prdiff.plan(_files(("src/a.py", 20), ("tests/test_a.py", 30)))
        self.assertEqual((result["depth"], result["questions"]), ("quick", 0))

    def test_a_risky_path_is_deep_whatever_the_size(self) -> None:
        for path in (
            ".github/workflows/ci.yml",
            "src/auth/session.py",
            "migrations/0054_x.py",
            "deploy/helm/values.yaml",
            "Dockerfile",
            "src/payments/charge.py",
            "config/credentials.json",
            "app/permissions.py",
            "src/authentication/login.py",
            "src/oauth/flow.py",
            "db/migrate/001.rb",
            "Data/Migrations/X.cs",
            "src/Auth/x.ts",
            "api/schema.graphql",
            "src/schemas/user.py",
        ):
            with self.subTest(path=path):
                result = prdiff.plan(_files((path, 2)))
                self.assertEqual(result["depth"], "deep")
                self.assertEqual(result["questions"], 12)
                self.assertIn(path, result["risky"])

    def test_a_word_inside_a_name_is_not_a_risky_path(self) -> None:
        # "author" contains "auth"; "tokenizer" contains "token"; a doc that
        # only names a schema is not one.
        result = prdiff.plan(
            _files(
                ("src/author_list.py", 10),
                ("src/tokenizer.py", 10),
                ("docs/schema_notes.md", 1),
            )
        )
        self.assertEqual(result["risky"], [])

    def test_medium_is_standard_and_huge_is_deep(self) -> None:
        self.assertEqual(prdiff.plan(_files(("src/a.py", 200)))["depth"], "standard")
        self.assertEqual(
            prdiff.plan(_files(*[(f"src/m{i}.py", 10) for i in range(4)]))["depth"],
            "standard",
        )
        self.assertEqual(prdiff.plan(_files(("src/a.py", 2000)))["depth"], "deep")

    def test_generated_and_binary_files_do_not_count(self) -> None:
        files = [
            *_files(("src/a.py", 10)),
            {
                "path": "uv.lock",
                "added": 5000,
                "deleted": 0,
                "generated": True,
                "binary": False,
            },
            {
                "path": "img.png",
                "added": 0,
                "deleted": 0,
                "generated": False,
                "binary": True,
            },
        ]
        result = prdiff.plan(files)
        self.assertEqual(
            (result["depth"], result["files"], result["lines"]), ("quick", 1, 10)
        )

    def test_asked_depth_and_question_count_win(self) -> None:
        result = prdiff.plan(_files((".github/workflows/ci.yml", 2)), depth="quick")
        self.assertEqual((result["depth"], result["questions"]), ("quick", 0))
        result = prdiff.plan(_files(("src/a.py", 10)), depth="quick", questions=3)
        self.assertEqual(result["questions"], 3)
        with self.assertRaises(prdiff.PayloadError):
            prdiff.plan(_files(("src/a.py", 10)), depth="lite")
        with self.assertRaises(prdiff.PayloadError):
            prdiff.plan(_files(("src/a.py", 10)), questions=-1)

    def test_an_invalid_risk_pattern_is_refused_not_a_traceback(self) -> None:
        with self.assertRaises(prdiff.PayloadError):
            prdiff.plan(_files(("src/a.py", 10)), risk="(auth")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "files.json"
            src.write_text(json.dumps(_files(("src/a.py", 10))), encoding="utf-8")
            self.assertEqual(prdiff.main(["plan", str(src), "--risk", "(auth"]), 2)

    def test_a_custom_risk_pattern_replaces_the_default(self) -> None:
        result = prdiff.plan(_files(("src/console/server.py", 10)), risk=r"console/")
        self.assertEqual(result["depth"], "deep")


class ExportTests(unittest.TestCase):
    def test_the_unattended_shape(self) -> None:
        doc = {
            "summary": "Adds a route.",
            "findings": [
                _finding() | {"title": "small", "worth": 2},
                _finding("importante") | {"title": "big", "suggestion": "Guard it."},
            ],
        }
        result = prdiff.export(doc, sha=SHA)
        self.assertEqual(result["verdict"], "changes_suggested")
        self.assertEqual(result["commit"], SHA)
        # Highest worth first.
        self.assertEqual([f["title"] for f in result["findings"]], ["big", "small"])
        self.assertEqual(
            set(result["findings"][0]),
            {
                "file",
                "line",
                "severity",
                "worth",
                "title",
                "quote",
                "explanation",
                "suggestion",
            },
        )
        self.assertEqual(result["findings"][0]["suggestion"], "Guard it.")
        # severity is its own field: the posting label is not repeated.
        self.assertEqual(result["findings"][0]["explanation"], "algo.")

    def test_a_finding_without_a_title_is_refused(self) -> None:
        for title in (None, "", "   "):
            finding = _finding()
            if title is not None:
                finding["title"] = title
            with self.subTest(title=title), self.assertRaises(prdiff.PayloadError):
                prdiff.export({"summary": "x", "findings": [finding]}, sha=SHA)

    def test_clean_and_refusals(self) -> None:
        self.assertEqual(
            prdiff.export({"summary": "Fine."}, sha=SHA)["verdict"], "clean"
        )
        with self.assertRaises(prdiff.PayloadError):
            prdiff.export({"summary": ""}, sha=SHA)
        with self.assertRaises(prdiff.PayloadError):
            prdiff.export({"summary": "x"}, sha="HEAD")
        with self.assertRaises(prdiff.PayloadError):
            prdiff.export(
                {
                    "summary": "x",
                    "findings": [_finding() | {"quote": "", "title": "t"}],
                },
                sha=SHA,
            )

    def test_cli_writes_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "findings.json"
            src.write_text(json.dumps({"summary": "Fine."}), encoding="utf-8")
            out = Path(tmp) / "review.json"
            self.assertEqual(
                prdiff.main(["export", str(src), "--sha", SHA, "-o", str(out)]), 0
            )
            self.assertEqual(json.loads(out.read_text())["verdict"], "clean")
            self.assertEqual(prdiff.main(["export", str(src), "--sha", "bad"]), 2)


if __name__ == "__main__":
    unittest.main()
