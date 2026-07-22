"""SWE-bench Pro dataset client (image-based hard cases).

Single source of truth for fetching SWE-bench Pro instances from the
HuggingFace datasets-server and the per-instance evaluation assets
(``run_script.sh`` + ``parser.py``) from the official OSS repo — stdlib only,
mirroring ``swebench_client.py``. See ``docs/design-swe-bench-pro-lane.md``.

Pro differs from Lite/Verified in three ways that matter here:

- ``fail_to_pass`` / ``pass_to_pass`` are Python-repr strings, not JSON
  (parse with ``ast.literal_eval``);
- each instance has a prebuilt amd64 Docker image on Docker Hub whose tag is
  *derived* from the instance id (128-char truncation + special cases — ported
  from the official ``helper_code/image_uri.py``), with the subject repo at
  ``/app``;
- the prompt payload includes ``requirements`` and ``interface`` sections.
"""

from __future__ import annotations

import ast
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

HF_ROWS_URL = (
    "https://datasets-server.huggingface.co/rows"
    "?dataset=ScaleAI%2FSWE-bench_Pro&config=default&split=test"
    "&offset={offset}&length={length}"
)
DATASET_SIZE = 731

# Per-instance evaluation assets in the official OSS repo.
RUN_ASSETS_URL = (
    "https://raw.githubusercontent.com/scaleapi/SWE-bench_Pro-os/main"
    "/run_scripts/{instance_id}/{asset}"
)

DOCKERHUB_USERNAME = "jefzda"
# Only Python instances are supported by the lane for now (phase 3: go/js/ts).
SUPPORTED_LANGUAGES = {"python"}


class SwebenchProFetchError(RuntimeError):
    """The HF datasets-server or GitHub raw could not be reached."""


def _fetch_json(url: str, timeout: float, attempts: int = 3) -> dict:
    """GET a JSON document with small retries (the HF rows API 502s sporadically)."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise SwebenchProFetchError(f"fetch failed ({url}): {last_exc}") from last_exc


def _clean_text(value: str) -> str:
    """Normalize a Pro text column that may arrive JSON-string-escaped.

    Some rows store problem_statement/requirements/interface as a quoted string
    with literal ``\\n`` sequences instead of real newlines; unescape only when
    the text clearly is in that form (no real newlines but escaped ones).
    """
    text = value.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n").replace('\\"', '"')
    return text


def _literal_list(value: str | list) -> list[str]:
    """Pro list columns arrive as Python-repr strings (sometimes JSON)."""
    if isinstance(value, list):
        return [str(v) for v in value]
    text = value.strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError(f"expected a list, got {type(parsed).__name__}: {text[:80]}")
    return [str(v) for v in parsed]


def image_uri(instance_id: str, repo: str, username: str = DOCKERHUB_USERNAME) -> str:
    """Docker Hub image URI for an instance (port of helper_code/image_uri.py).

    Tag = ``<org>.<repo>-<instance id sans "instance_" prefix>``, with the
    ``-vnan`` suffix stripped, element-web special-cased, and the whole tag
    truncated to Docker Hub's 128-char limit.
    """
    repo_base, repo_name_only = repo.lower().split("/")
    hsh = instance_id.removeprefix("instance_")

    if (
        instance_id
        == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan"
    ):
        repo_name_only = "element-web"  # upstream keeps the full name here
    elif "element-hq" in repo.lower() and "element-web" in repo.lower():
        repo_name_only = "element"
        hsh = hsh.removesuffix("-vnan")
    else:
        hsh = hsh.removesuffix("-vnan")

    tag = f"{repo_base}.{repo_name_only}-{hsh}"[:128]
    return f"{username}/sweap-images:{tag}"


def fetch_instance(instance_id: str, timeout: float = 30.0) -> dict:
    """Fetch one raw SWE-bench Pro row from the HF datasets-server.

    Raises SwebenchProFetchError on network/server trouble and ValueError when
    the dataset was readable but the instance does not exist.
    """
    for offset in range(0, DATASET_SIZE, 100):
        data = _fetch_json(HF_ROWS_URL.format(offset=offset, length=100), timeout)
        for row in data.get("rows", []):
            record = row["row"]
            if record["instance_id"] == instance_id:
                return record
    raise ValueError(f"Instance not found in SWE-bench Pro: {instance_id}")


def fetch_run_assets(instance_id: str, timeout: float = 30.0) -> dict[str, str]:
    """Fetch the per-instance run_script.sh + parser.py from the OSS repo."""
    assets: dict[str, str] = {}
    for asset in ("run_script.sh", "parser.py"):
        url = RUN_ASSETS_URL.format(instance_id=instance_id, asset=asset)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310
                assets[asset] = resp.read().decode()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SwebenchProFetchError(
                f"SWE-bench Pro run asset fetch failed ({url}): {exc}"
            ) from exc
    return assets


# The dataset is static (731 published rows), so the browse catalog is cached
# on disk and refreshed at most daily; data/tmp/ is gitignored. Bump the file
# name when the entry schema changes so stale caches are never half-shaped.
_CATALOG_CACHE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "tmp"
    / "swebench_pro_catalog_v2.json"
)
_CATALOG_TTL_SECONDS = 24 * 3600

# Section-header lines that carry no content of their own in the Pro issue
# templates ("## Description", "Problem / Opportunity", "SUMMARY", …).
_HEADER_NOISE = {
    "title",
    "description",
    "summary",
    "problem",
    "problem / opportunity",
    "bug report",
    "bug",
    "feature request",
    "issue type",
    "component name",
    "expected behavior",
    "expected behaviour",
    "actual behavior",
    "actual behaviour",
    "steps to reproduce",
    "reproducing the bug",
    "label",
}


# Reaching one of these headers means the prose part is over — what follows is
# repro commands / numbered steps, which read as noise in a short summary.
_SUMMARY_STOP_HEADERS = {
    "steps to reproduce",
    "reproducing the bug",
    "how to reproduce",
}


def _sentence_cap(text: str, budget: int = 300) -> str:
    """Truncate at a sentence boundary (never mid-sentence) within budget."""
    if len(text) <= budget:
        return text
    cut = text[:budget]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > 80:
            return cut[: idx + 1].strip()
    return cut[: cut.rfind(" ")].rstrip(",;:") + "…"


def title_and_summary(text: str) -> tuple[str, str]:
    """Extract a display title + short summary from a Pro problem statement.

    The statements come in several framings (markdown headers, ``Title:``
    prefixes, single-line issues with inline ``##`` separators); this walks the
    cleaned lines, skips bare section headers, takes the first real line as the
    title and compacts the following prose into a sentence-capped summary
    (stopping before repro-steps sections).
    """
    lines = [
        line.strip().strip('"*#').strip() for line in _clean_text(text).splitlines()
    ]
    lines = [line for line in lines if line]
    title = ""
    rest: list[str] = []
    for line in lines:
        norm = line.lower().rstrip(".:").strip()
        if norm in _SUMMARY_STOP_HEADERS:
            if title:
                break
            continue
        if not title:
            if norm in _HEADER_NOISE:
                continue
            candidate = line
            if candidate.lower().startswith("title:"):
                candidate = candidate[6:].strip()
            # Single-line statements keep their sections inline ("… ## Description …").
            parts = [p.strip() for p in candidate.split(" ## ") if p.strip()]
            title = parts[0]
            rest.extend(parts[1:])
        elif norm in _HEADER_NOISE:
            continue
        else:
            rest.append(line)
    summary_words = " ".join(" ".join(rest).split())
    for noise in ("Description", "Problem / Opportunity"):
        summary_words = summary_words.removeprefix(noise).lstrip(" :")
    summary_words = summary_words.lstrip(" .—-:")
    return title[:160], _sentence_cap(summary_words)


def _summarize_row(record: dict) -> dict:
    """Compact browse-catalog entry for one dataset row."""
    title, summary = title_and_summary(record.get("problem_statement", ""))
    try:
        categories = _literal_list(record.get("issue_categories", "[]"))
    except (ValueError, json.JSONDecodeError, SyntaxError):
        categories = []
    return {
        "instance_id": record["instance_id"],
        "repo": record["repo"],
        "repo_language": record.get("repo_language", ""),
        "title": title,
        "summary": summary,
        "issue_categories": categories,
        "f2p_count": len(_literal_list(record.get("fail_to_pass", "[]"))),
        "p2p_count": len(_literal_list(record.get("pass_to_pass", "[]"))),
    }


def list_instances(
    timeout: float = 30.0, max_age_seconds: int | None = None
) -> list[dict]:
    """Browse catalog of the supported (Python) SWE-bench Pro instances.

    One sweep over the dataset (8 datasets-server pages), cached on disk so the
    UI's browse panel doesn't re-crawl HuggingFace on every visit. Entries are
    summaries — importing still fetches the full row + run assets.
    """
    ttl = _CATALOG_TTL_SECONDS if max_age_seconds is None else max_age_seconds
    if _CATALOG_CACHE.exists():
        age = time.time() - _CATALOG_CACHE.stat().st_mtime
        if age < ttl:
            try:
                cached = json.loads(_CATALOG_CACHE.read_text())
                if isinstance(cached, list) and cached:
                    return cached
            except (json.JSONDecodeError, OSError):
                pass  # fall through to a fresh crawl

    catalog: list[dict] = []
    for offset in range(0, DATASET_SIZE, 100):
        data = _fetch_json(HF_ROWS_URL.format(offset=offset, length=100), timeout)
        for row in data.get("rows", []):
            record = row["row"]
            if record.get("repo_language") in SUPPORTED_LANGUAGES:
                catalog.append(_summarize_row(record))

    catalog.sort(key=lambda item: (item["repo"], item["instance_id"]))
    _CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _CATALOG_CACHE.write_text(json.dumps(catalog))
    return catalog


def normalize_instance(instance_id: str, timeout: float = 30.0) -> dict:
    """Fetch + normalize into the spec CaseService.import_swebench_pro_instance takes."""
    inst = fetch_instance(instance_id, timeout=timeout)
    language = inst.get("repo_language", "")
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Instance {instance_id} is '{language}'; the Pro lane currently "
            f"supports {sorted(SUPPORTED_LANGUAGES)} (see design doc, phase 3)."
        )
    assets = fetch_run_assets(instance_id, timeout=timeout)
    return {
        "instance_id": inst["instance_id"],
        "repo": inst["repo"],
        "repo_language": language,
        "base_commit": inst["base_commit"],
        "problem_statement": _clean_text(inst["problem_statement"]),
        "requirements": _clean_text(inst.get("requirements", "")),
        "interface": _clean_text(inst.get("interface", "")),
        "test_patch": inst["test_patch"],
        "fail_to_pass": _literal_list(inst["fail_to_pass"]),
        "pass_to_pass": _literal_list(inst["pass_to_pass"]),
        "selected_test_files_to_run": _literal_list(
            inst.get("selected_test_files_to_run", "[]")
        ),
        "before_repo_set_cmd": inst.get("before_repo_set_cmd", ""),
        "dockerhub_image": image_uri(inst["instance_id"], inst["repo"]),
        "run_script": assets["run_script.sh"],
        "parser": assets["parser.py"],
        # Reference fix — for golden-patch self-tests ONLY. Never persisted on
        # the Case / case_spec.json: the runner bind-mount makes that file
        # readable by the agent, and shipping the answer would void the bench.
        "gold_patch": inst.get("patch", ""),
    }
