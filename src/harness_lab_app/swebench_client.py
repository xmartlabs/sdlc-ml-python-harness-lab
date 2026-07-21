"""SWE-bench dataset client + per-repo environment specs.

Single source of truth for fetching instances from the HuggingFace
datasets-server (stdlib only, no `datasets` dependency) and for the small
per-repo install recipe the generic image-free runtime needs (ADR-0009).
Used by the API import endpoint, `scripts/import_swebench.py`, and the
runner/validation helpers in `scripts/swebench_common.py`.

Both the Lite (300) and Verified (500, human-validated) subsets are supported;
`fetch_instance` searches them in order, so any curated instance id resolves
regardless of which split it belongs to (instance ids are globally unique).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

HF_ROWS_URL = (
    "https://datasets-server.huggingface.co/rows"
    "?dataset={dataset}&config=default&split=test"
    "&offset={offset}&length={length}"
)
# (url-encoded dataset id, row count), searched in this order.
_DATASETS: tuple[tuple[str, int], ...] = (
    ("princeton-nlp%2FSWE-bench_Lite", 300),
    ("princeton-nlp%2FSWE-bench_Verified", 500),
)

# Per-repo environment recipe (the "SWE-bench install spec"). The HF rows don't
# carry dependency pins, so we keep a small map of the known-good Python version
# and extra pip constraints per supported repo. Adding a new repo = add its
# entry here (that's the price of the generic, image-free runtime).
_ENV_SPECS: dict[str, dict] = {
    # flask 2.3 @ base_commit imports werkzeug.urls.url_quote (removed in
    # werkzeug 3.0), so pin Werkzeug<3; needs Python 3.11.
    "pallets/flask": {"python": "3.11", "pins": ["Werkzeug<3"]},
}
_DEFAULT_ENV = {"python": "3.11", "pins": []}


class SwebenchFetchError(RuntimeError):
    """The HF datasets-server could not be reached or answered abnormally."""


def env_spec(repo: str) -> dict:
    return _ENV_SPECS.get(repo, _DEFAULT_ENV)


def repo_url_from(repo: str) -> str:
    """`pallets/flask` -> the public GitHub clone URL."""
    return f"https://github.com/{repo}.git"


def fetch_instance(instance_id: str, timeout: float = 30.0) -> dict:
    """Fetch one SWE-bench instance (Lite or Verified) from the HF datasets-server.

    Raises SwebenchFetchError on network/server trouble and ValueError when the
    datasets were readable but the instance does not exist in any of them.
    """
    for dataset, size in _DATASETS:
        for offset in range(0, size, 100):
            url = HF_ROWS_URL.format(dataset=dataset, offset=offset, length=100)
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310
                    data = json.load(resp)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                raise SwebenchFetchError(
                    f"SWE-bench fetch failed ({url}): {exc}"
                ) from exc
            for row in data.get("rows", []):
                record = row["row"]
                if record["instance_id"] == instance_id:
                    return record
    raise ValueError(f"Instance not found in SWE-bench Lite/Verified: {instance_id}")


def normalize_instance(instance_id: str, timeout: float = 30.0) -> dict:
    """Fetch + normalize into the spec CaseService.import_swebench_instance takes."""
    inst = fetch_instance(instance_id, timeout=timeout)
    spec = env_spec(inst["repo"])
    return {
        "instance_id": inst["instance_id"],
        "repo": inst["repo"],
        "base_commit": inst["base_commit"],
        "problem_statement": inst["problem_statement"],
        "test_patch": inst["test_patch"],
        "fail_to_pass": json.loads(inst["FAIL_TO_PASS"]),
        "pass_to_pass": json.loads(inst["PASS_TO_PASS"]),
        "version": inst.get("version", ""),
        "repo_url": repo_url_from(inst["repo"]),
        "python": spec["python"],
        "install_pins": spec["pins"],
    }
