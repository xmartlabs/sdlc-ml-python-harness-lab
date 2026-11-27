"""Models a serving host can download, found before they are on it (ADR-0101).

Two sources feed *Get more models* on the Your server step:

- ``CURATED``: a short, hand-picked list of models that run an agent (tool
  use) on small machines, with each file's size checked against its Hugging
  Face repo. Offline: no request leaves the lab to show it.
- Hugging Face search: the public ``/api/models`` listing filtered to GGUF
  repos, then each repo's file tree for its quantizations and sizes.

Every quantization carries what to ask each server for. Ollama pulls a
library tag when the curated entry names one, otherwise
``hf.co/{repo}:{quant}``; LM Studio downloads the Hugging Face link with the
quantization beside it. Both servers accept those forms natively.

Ollama's own library has no public search API, so it is not searched here; a
library tag can still be typed by name on the page.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from harness_lab_app.direct_hosts import TtlCache, as_int
from harness_lab_app.local_tuning.catalog import CONTEXT_OPTIONS, DEFAULT_LOAD, is_moe
from harness_lab_app.local_tuning.contracts import (
    HubModel,
    HubQuant,
    MachineSpec,
    ModelInfo,
    Verdict,
)
from harness_lab_app.local_tuning.memory_estimate import estimate_memory
from harness_lab_app.local_tuning.servers import OLLAMA_SPLIT_GGUF_MIN_VERSION

HF_ROOT = "https://huggingface.co"
_HF_TIMEOUT_SECONDS = 8.0
_SEARCH_LIMIT = 8
_SEARCH_CACHE_SECONDS = 600.0
_MAX_QUERY_CHARS = 80

# --- Curated list -----------------------------------------------------------
# Sizes are the GGUF file sizes in each repo (Hugging Face /api/models/{repo}/
# tree/main, checked 2026-11-26). An Ollama tag is given only for the
# quantization its library default ships (Q4_K_M, or MXFP4 for gpt-oss); the
# other quantizations are pulled from Hugging Face. ``tools`` follows the
# model card: Gemma 3 has no native tool calling, so an agent cannot use it.

_CURATED_SPECS: list[dict] = [
    {
        "repo": "Qwen/Qwen3-4B-GGUF",
        "label": "Qwen3 4B",
        "params": "4B",
        "arch": "qwen3",
        "tools": True,
        "max_context": 40960,
        "quants": [("Q4_K_M", 2_500_000_000, "qwen3:4b"), ("Q8_0", 4_280_000_000, "")],
    },
    {
        "repo": "Qwen/Qwen3-8B-GGUF",
        "label": "Qwen3 8B",
        "params": "8B",
        "arch": "qwen3",
        "tools": True,
        "max_context": 40960,
        "quants": [("Q4_K_M", 5_030_000_000, "qwen3:8b"), ("Q8_0", 8_710_000_000, "")],
    },
    {
        "repo": "Qwen/Qwen3-14B-GGUF",
        "label": "Qwen3 14B",
        "params": "14B",
        "arch": "qwen3",
        "tools": True,
        "max_context": 40960,
        "quants": [
            ("Q4_K_M", 9_000_000_000, "qwen3:14b"),
            ("Q8_0", 15_700_000_000, ""),
        ],
    },
    {
        "repo": "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        "label": "Qwen3 Coder 30B-A3B",
        "params": "30B-A3B",
        "arch": "qwen3moe",
        "tools": True,
        "max_context": 262144,
        "quants": [
            ("Q3_K_M", 14_710_000_000, ""),
            ("Q4_K_M", 18_560_000_000, "qwen3-coder:30b"),
        ],
    },
    {
        "repo": "lmstudio-community/Qwen2.5-Coder-7B-Instruct-GGUF",
        "label": "Qwen2.5 Coder 7B",
        "params": "7B",
        "arch": "qwen2",
        "tools": True,
        "max_context": 32768,
        "quants": [
            ("Q4_K_M", 4_680_000_000, "qwen2.5-coder:7b"),
            ("Q8_0", 8_100_000_000, ""),
        ],
    },
    {
        "repo": "lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF",
        "label": "Llama 3.1 8B",
        "params": "8B",
        "arch": "llama",
        "tools": True,
        "max_context": 131072,
        "quants": [
            ("Q4_K_M", 4_920_000_000, "llama3.1:8b"),
            ("Q8_0", 8_540_000_000, ""),
        ],
    },
    {
        "repo": "lmstudio-community/gpt-oss-20b-GGUF",
        "label": "gpt-oss 20B",
        "params": "20B",
        "arch": "gpt-oss",
        "tools": True,
        "max_context": 131072,
        "quants": [("MXFP4", 12_110_000_000, "gpt-oss:20b")],
    },
    # Gemma 4: the library builds need Ollama 0.30.9 (their manifest says so)
    # and the Hugging Face files ship a separate vision file, which Ollama
    # loads only from 0.30 on. Sizes from unsloth's repo; tools per the chat
    # template and Ollama's capabilities.
    {
        "repo": "unsloth/gemma-4-E2B-it-GGUF",
        "label": "Gemma 4 E2B",
        "params": "2B",
        "arch": "gemma4",
        "tools": True,
        "max_context": 131072,
        "quants": [
            ("Q4_K_M", 3_106_738_272, "gemma4:e2b", "0.30.9"),
            ("Q8_0", 5_048_352_864, "", "0.30.0"),
        ],
    },
    {
        "repo": "unsloth/gemma-4-E4B-it-GGUF",
        "label": "Gemma 4 E4B",
        "params": "4B",
        "arch": "gemma4",
        "tools": True,
        "max_context": 131072,
        "quants": [
            ("Q4_K_M", 4_977_171_584, "gemma4:e4b", "0.30.9"),
            ("Q8_0", 8_192_953_472, "", "0.30.0"),
        ],
    },
    {
        "repo": "lmstudio-community/gemma-3-4b-it-GGUF",
        "label": "Gemma 3 4B",
        "params": "4B",
        "arch": "gemma3",
        "tools": False,
        "max_context": 131072,
        "quants": [("Q4_K_M", 2_490_000_000, "gemma3:4b"), ("Q8_0", 4_130_000_000, "")],
    },
]


class HubUnavailable(RuntimeError):
    """Hugging Face could not be reached or answered with an error."""


def _hf_link(repo: str) -> str:
    return f"{HF_ROOT}/{repo}"


# The quantizations most people pick: a good size/quality trade-off that
# every runtime loads. Listed first; 1-2 bit, importance-matrix and
# full-precision files follow, still selectable.
_COMMON_QUANTS = frozenset(
    {
        "Q3_K_M",
        "Q4_0",
        "Q4_K_S",
        "Q4_K_M",
        "UD-Q4_K_XL",
        "Q5_K_M",
        "Q6_K",
        "Q8_0",
        "MXFP4",
    }
)


def _quant(
    repo: str,
    name: str,
    size_bytes: int | None,
    ollama_tag: str = "",
    min_ollama_version: str = "",
) -> HubQuant:
    return HubQuant(
        name=name,
        size_bytes=size_bytes,
        ollama_model=ollama_tag or f"hf.co/{repo}:{name}",
        lmstudio_model=_hf_link(repo),
        common=name.upper() in _COMMON_QUANTS,
        min_ollama_version=min_ollama_version,
    )


def _order(quants: list[HubQuant]) -> list[HubQuant]:
    """Common quantizations first, each group smallest first."""
    return sorted(quants, key=lambda q: (not q.common, q.size_bytes or 0))


def curated_models() -> list[HubModel]:
    """The curated list, without fit verdicts."""
    return [
        HubModel(
            source="curated",
            repo=spec["repo"],
            label=spec["label"],
            params=spec["params"],
            arch=spec["arch"],
            supports_tools=spec["tools"],
            max_context=spec["max_context"],
            quants=[_quant(spec["repo"], *q) for q in spec["quants"]],
        )
        for spec in _CURATED_SPECS
    ]


# --- Hugging Face search ------------------------------------------------------

# A quantization at the end of a GGUF file name: "Qwen3-8B-Q4_K_M.gguf",
# "...-UD-Q4_K_XL.gguf", "gpt-oss-20b-MXFP4.gguf", "...-IQ3_XXS.gguf".
_QUANT_PATTERN = re.compile(
    r"[-_.]((?:UD-)?(?:I?Q\d[A-Z0-9_]*|BF16|F16|F32|MXFP4))\.gguf$", re.IGNORECASE
)
# Split files ("-00001-of-00002.gguf") need every part; Ollama's hf.co pull
# takes one file, so they are left out rather than offered and failing.
_SHARD_PATTERN = re.compile(r"-\d{5}-of-\d{5}\.gguf$", re.IGNORECASE)
_PARAMS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)B(?:-A(\d+(?:\.\d+)?)B)?", re.IGNORECASE)

_search_cache = TtlCache()


def _fetch(url: str) -> object:
    """GET a Hugging Face JSON document. The seam tests replace."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "harness-lab"}
    )
    try:
        with urllib.request.urlopen(  # nosec B310 - fixed https host
            req, timeout=_HF_TIMEOUT_SECONDS
        ) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise HubUnavailable(f"Hugging Face answered {exc.code} for {url}.") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as exc:
        raise HubUnavailable(
            "Could not reach Hugging Face from the lab. The API container needs "
            "internet access to search; the recommended list and exact names "
            "still work."
        ) from exc


def params_from_name(name: str) -> str:
    """Parameter label from a repo name: "Qwen3-Coder-30B-A3B-..." -> "30B-A3B"."""
    match = _PARAMS_PATTERN.search(name.split("/")[-1])
    if not match:
        return ""
    total = match.group(1)
    active = match.group(2)
    return f"{total}B-A{active}B" if active else f"{total}B"


def quants_from_tree(repo: str, tree: object) -> list[HubQuant]:
    """Single-file GGUF quantizations in a repo's top-level file tree."""
    if not isinstance(tree, list):
        return []
    quants: list[HubQuant] = []
    seen: set[str] = set()
    for entry in tree:
        if not isinstance(entry, dict) or entry.get("type") not in (None, "file"):
            continue
        path = str(entry.get("path") or "")
        if "/" in path or not path.lower().endswith(".gguf"):
            continue
        if path.lower().startswith("mmproj") or _SHARD_PATTERN.search(path):
            continue
        match = _QUANT_PATTERN.search(path)
        if not match:
            continue
        name = match.group(1).upper()
        if name in seen:
            continue
        seen.add(name)
        quants.append(_quant(repo, name, as_int(entry.get("size"))))
    return _order(quants)


def has_separate_projector(tree: object) -> bool:
    """True when the repo ships its vision weights as their own mmproj file."""
    if not isinstance(tree, list):
        return False
    return any(
        isinstance(entry, dict)
        and "/" not in str(entry.get("path") or "")
        and str(entry.get("path") or "").lower().startswith("mmproj")
        for entry in tree
    )


def _hub_model_from_listing(entry: dict, tree: object) -> HubModel | None:
    repo = str(entry.get("id") or entry.get("modelId") or "")
    if not repo:
        return None
    quants = quants_from_tree(repo, tree)
    if not quants:
        return None
    raw_gguf = entry.get("gguf")
    gguf: dict = raw_gguf if isinstance(raw_gguf, dict) else {}
    template = str(gguf.get("chat_template") or "")
    split = has_separate_projector(tree)
    if split:
        # Ollama's hf.co pull fetches the mmproj file too, and Ollama before
        # 0.30 cannot load that split layout for recent architectures.
        for quant in quants:
            quant.min_ollama_version = OLLAMA_SPLIT_GGUF_MIN_VERSION
    return HubModel(
        source="huggingface",
        repo=repo,
        label=repo.split("/")[-1].removesuffix("-GGUF").removesuffix("-gguf"),
        params=params_from_name(repo),
        arch=str(gguf.get("architecture") or ""),
        # A chat template that renders ``tools`` is the only signal the hub
        # gives; without one the model may still call tools, so it stays unknown.
        supports_tools=True if "tools" in template else None,
        downloads=as_int(entry.get("downloads")),
        max_context=as_int(gguf.get("context_length")),
        quants=quants,
        separate_projector=split,
    )


def search_huggingface(query: str) -> list[HubModel]:
    """GGUF repos matching ``query``, most downloaded first, with their files."""
    text = " ".join(query.split())[:_MAX_QUERY_CHARS]
    if not text:
        return []
    cached = _search_cache.get(text.lower())
    if isinstance(cached, list):
        return [m.model_copy(deep=True) for m in cached]
    params = urllib.parse.urlencode(
        [
            ("search", text),
            ("filter", "gguf"),
            ("sort", "downloads"),
            ("direction", "-1"),
            ("limit", str(_SEARCH_LIMIT)),
            ("expand[]", "gguf"),
            ("expand[]", "downloads"),
        ]
    )
    listing = _fetch(f"{HF_ROOT}/api/models?{params}")
    entries = (
        [e for e in listing if isinstance(e, dict)] if isinstance(listing, list) else []
    )

    def tree_of(entry: dict) -> object:
        repo = urllib.parse.quote(str(entry.get("id") or ""), safe="/")
        try:
            return _fetch(f"{HF_ROOT}/api/models/{repo}/tree/main")
        except HubUnavailable:
            return []

    with ThreadPoolExecutor(max_workers=_SEARCH_LIMIT) as pool:
        trees = list(pool.map(tree_of, entries))
    models = [
        model
        for entry, tree in zip(entries, trees, strict=True)
        if (model := _hub_model_from_listing(entry, tree)) is not None
    ]
    _search_cache.set(text.lower(), models, _SEARCH_CACHE_SECONDS)
    return [m.model_copy(deep=True) for m in models]


# --- Fit -----------------------------------------------------------------------


def _largest_fitting_context(info: ModelInfo, machine: MachineSpec) -> int | None:
    limit = info.max_context or max(CONTEXT_OPTIONS)
    for context in sorted(CONTEXT_OPTIONS, reverse=True):
        if context > limit:
            continue
        config = DEFAULT_LOAD.model_copy(update={"context_length": context})
        if estimate_memory(info, config, machine).verdict != Verdict.fail:
            return context
    return None


def with_fit(models: list[HubModel], machine: MachineSpec | None) -> list[HubModel]:
    """Annotate each quantization with the memory estimate on ``machine``.

    ``verdict`` uses the lab's default load configuration (32k context, flash
    attention), the one step 3 starts from; ``fit_context`` is the largest
    context option that still fits, for machines where 32k does not.
    """
    if machine is None:
        return models
    for model in models:
        for quant in model.quants:
            if not quant.size_bytes:
                continue
            info = ModelInfo(
                id=quant.ollama_model,
                arch=model.arch,
                params=model.params,
                quantization=quant.name,
                size_bytes=quant.size_bytes,
                max_context=model.max_context,
                is_moe=is_moe(model.params),
            )
            estimate = estimate_memory(info, DEFAULT_LOAD, machine)
            quant.verdict = estimate.verdict
            quant.total_gb = round(estimate.breakdown.total_gb, 1)
            quant.fit_context = _largest_fitting_context(info, machine)
    return models
