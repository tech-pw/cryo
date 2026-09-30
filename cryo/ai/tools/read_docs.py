"""Runbook and feature help from docs/*.md. No match means abstain."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pydantic import BaseModel

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.read_identity import clamp_limit, make_envelope
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Role

_DOCS = Path(__file__).resolve().parents[3] / "docs"


class DocsInput(BaseModel):
    query: str = ""
    limit: int = 5


class FeatureInput(BaseModel):
    feature: str = ""
    query: str = ""


def _product_version() -> str:
    try:
        return version("cryo")
    except PackageNotFoundError:
        return "unknown"


def _docs() -> list[Path]:
    if not _DOCS.is_dir():
        return []
    found = []
    for path in sorted(_DOCS.rglob("*.md")):
        if "_private-pw" in path.parts:
            continue
        found.append(path)
    return found


def _search(query: str, limit: int) -> list[dict[str, str]]:
    tokens = [token.lower() for token in query.split() if len(token) > 2]
    if not tokens:
        return []
    matches: list[tuple[int, dict[str, str]]] = []
    version_label = _product_version()
    for path in _docs():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = text.lower()
        hits = sum(lowered.count(token) for token in tokens)
        if hits == 0:
            continue
        index = min(lowered.find(token) for token in tokens if token in lowered)
        start = max(0, index - 120)
        excerpt = " ".join(text[start : start + 400].split())
        relative = str(path.relative_to(_DOCS.parents[0]))
        matches.append(
            (
                hits,
                {
                    "source_path": relative,
                    "version": version_label,
                    "excerpt": excerpt,
                },
            )
        )
    matches.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in matches[:limit]]


def search_runbooks(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = DocsInput.model_validate(parsed.model_dump())
    query = spec.query.strip()
    if not query:
        return make_envelope(
            "search_runbooks",
            {"matches": [], "abstained": True},
            ctx=ctx,
            classification="unknown",
            error_category="abstain",
            warnings=["No query. Abstaining rather than guessing a runbook."],
            measurement_method="docs",
        )
    limit = clamp_limit(spec.limit, default=5, maximum=20)
    matches = _search(query, limit)
    if not matches:
        return make_envelope(
            "search_runbooks",
            {"matches": [], "abstained": True, "query": query, "version": _product_version()},
            ctx=ctx,
            classification="unknown",
            error_category="abstain",
            warnings=["No matching runbook under docs/. Abstaining."],
            measurement_method="docs",
            coverage={"searched": "docs/**/*.md", "excluded": "docs/_private-pw"},
        )
    return make_envelope(
        "search_runbooks",
        {
            "summary": "Matching docs excerpts. Version and source path are included.",
            "matches": matches,
            "query": query,
            "version": _product_version(),
            "abstained": False,
        },
        ctx=ctx,
        source_links=[{"rel": "doc", "href": item["source_path"]} for item in matches],
        coverage={
            "returned": len(matches),
            "searched": "docs/**/*.md",
            "excluded": "docs/_private-pw",
        },
        row_count=len(matches),
        measurement_method="docs",
    )


def get_feature_help(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = FeatureInput.model_validate(parsed.model_dump())
    query = (spec.query or spec.feature).strip()
    if not query:
        return make_envelope(
            "get_feature_help",
            {"matches": [], "abstained": True},
            ctx=ctx,
            classification="unknown",
            error_category="abstain",
            warnings=["No feature named. Abstaining."],
            measurement_method="docs",
        )
    matches = _search(query, 5)
    if not matches:
        return make_envelope(
            "get_feature_help",
            {
                "matches": [],
                "abstained": True,
                "feature": spec.feature or query,
                "version": _product_version(),
            },
            ctx=ctx,
            classification="unknown",
            error_category="abstain",
            warnings=[f"No docs match for {query}. Abstaining rather than inventing help."],
            measurement_method="docs",
        )
    return make_envelope(
        "get_feature_help",
        {
            "summary": "Feature help quoted from docs. Not an invented explanation.",
            "feature": spec.feature or query,
            "matches": matches,
            "version": _product_version(),
            "abstained": False,
        },
        ctx=ctx,
        source_links=[{"rel": "doc", "href": item["source_path"]} for item in matches],
        coverage={"returned": len(matches), "version": _product_version()},
        row_count=len(matches),
        measurement_method="docs",
    )


_ICEBERG_PAGES = {
    "maintenance": "https://iceberg.apache.org/docs/latest/maintenance/",
    "procedures": "https://iceberg.apache.org/docs/latest/spark-procedures/",
    "spec": "https://iceberg.apache.org/spec/",
    "default": "https://iceberg.apache.org/docs/latest/",
}


def _iceberg_url(query: str) -> str:
    lowered = query.lower()
    if any(token in lowered for token in ("expire", "orphan", "rewrite", "compact", "optimize")):
        return _ICEBERG_PAGES["maintenance"]
    if "procedure" in lowered or "spark" in lowered:
        return _ICEBERG_PAGES["procedures"]
    if any(token in lowered for token in ("spec", "snapshot", "manifest", "schema")):
        return _ICEBERG_PAGES["spec"]
    return _ICEBERG_PAGES["default"]


def _excerpt(text: str, query: str) -> str:
    tokens = [token.lower() for token in query.split() if len(token) > 2]
    lowered = text.lower()
    index = 0
    for token in tokens:
        found = lowered.find(token)
        if found >= 0:
            index = found
            break
    start = max(0, index - 80)
    return " ".join(text[start : start + 500].split())


def search_iceberg_docs(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = DocsInput.model_validate(parsed.model_dump())
    query = spec.query.strip()
    if not query:
        return make_envelope(
            "search_iceberg_docs",
            {"matches": [], "abstained": True, "ask": "Which Iceberg behavior should I look up?"},
            ctx=ctx,
            classification="unknown",
            error_category="abstain",
            warnings=["No query. Ask the operator what to look up."],
            measurement_method="official_docs",
        )
    url = _iceberg_url(query)
    fetcher = getattr(ctx, "fetch_url", None)
    try:
        page = fetcher(url) if callable(fetcher) else _fetch_url(url)
    except (OSError, TimeoutError, ValueError):
        return make_envelope(
            "search_iceberg_docs",
            {"matches": [], "abstained": True, "url": url, "query": query},
            ctx=ctx,
            classification="unknown",
            error_category="abstain",
            warnings=[f"Could not read {url}. Ask a narrower question instead of guessing."],
            measurement_method="official_docs",
        )
    return make_envelope(
        "search_iceberg_docs",
        {
            "summary": "Excerpt from the official Apache Iceberg docs. Cite the URL.",
            "url": url,
            "excerpt": _excerpt(page, query),
            "query": query,
            "abstained": False,
        },
        ctx=ctx,
        source_links=[{"rel": "doc", "href": url}],
        row_count=1,
        measurement_method="official_docs",
    )


def _fetch_url(url: str) -> str:
    from urllib.request import urlopen

    with urlopen(url, timeout=8) as response:
        raw = response.read(80_000).decode("utf-8", errors="replace")
    text = []
    skip = False
    for char in raw:
        if char == "<":
            skip = True
            continue
        if char == ">":
            skip = False
            text.append(" ")
            continue
        if not skip:
            text.append(char)
    return "".join(text)


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_runbooks",
        family="docs",
        description="Search docs/*.md, excluding docs/_private-pw. No match abstains.",
        input_model=DocsInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=search_runbooks,
    ),
    ToolSpec(
        name="get_feature_help",
        family="docs",
        description="Feature help from docs. No match abstains.",
        input_model=FeatureInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_feature_help,
    ),
    ToolSpec(
        name="search_iceberg_docs",
        family="docs",
        description=(
            "Read an official Apache Iceberg page when the answer is not in Cryo. "
            "Use this before guessing spec or procedure behavior."
        ),
        input_model=DocsInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=search_iceberg_docs,
    ),
)
