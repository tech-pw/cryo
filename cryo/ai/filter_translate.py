"""Translate natural-language table queries into TableFilterState-compatible JSON."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from cryo.ai.runtime import LLMInvokeContext, complete_with_budget_and_observation

_VALID_GRADES = {"A", "B", "C", "D", "E", "F"}
_VALID_PRIORITIES = {"HIGH", "MED", "LOW"}
_ALLOWED_FIELDS = {
    "activeOnly",
    "grades",
    "priorities",
    "needsCompaction",
    "schemaSearch",
    "minSizeGb",
    "minSmallFiles",
    "minMonthlySavings",
    "minReclaimableGb",
}


@dataclass(slots=True)
class TranslationResult:
    filter: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    used_llm: bool = False
    provider: str | None = None
    model: str | None = None
    observation_id: str | None = None


def _normalize_number(value: str) -> float | None:
    cleaned = value.replace(",", "").strip().lower()
    multiplier = 1.0
    if cleaned.endswith("tb"):
        multiplier = 1024.0
        cleaned = cleaned[:-2].strip()
    elif cleaned.endswith("gb"):
        cleaned = cleaned[:-2].strip()
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def deterministic_translate(query: str) -> TranslationResult:
    text = query.strip()
    lower = text.lower()
    filt: dict[str, Any] = {"activeOnly": True}
    warnings: list[str] = []

    grades = sorted({g for g in _VALID_GRADES if re.search(rf"\bgrade\s*{g.lower()}\b", lower)})
    if not grades:
        grade_list_match = re.search(r"\bgrade[s]?\s*(?:is|in)?\s*([a-f,\s]+)", lower)
        if grade_list_match:
            parsed = {token.strip().upper() for token in grade_list_match.group(1).split(",")}
            grades = sorted(g for g in parsed if g in _VALID_GRADES)
    if grades:
        filt["grades"] = grades

    priorities = sorted(
        {
            p
            for p in _VALID_PRIORITIES
            if re.search(rf"\b(priority|priorities)\b.*\b{p.lower()}\b", lower)
        }
    )
    if priorities:
        filt["priorities"] = priorities

    if "needs compaction" in lower:
        filt["needsCompaction"] = True
    elif "healthy only" in lower or "no compaction" in lower:
        filt["needsCompaction"] = False

    if "all tables" in lower or "including inactive" in lower:
        filt["activeOnly"] = False

    savings = re.search(r"(?:savings|save)[^\d$]*\$?\s*([\d,]+(?:\.\d+)?)", lower)
    if savings:
        try:
            filt["minMonthlySavings"] = float(savings.group(1).replace(",", ""))
        except ValueError:
            warnings.append("Could not parse savings threshold.")

    small_files = re.search(r"small files?[^\d]*([\d,]+)", lower)
    if small_files:
        try:
            filt["minSmallFiles"] = int(float(small_files.group(1).replace(",", "")))
        except ValueError:
            warnings.append("Could not parse small-files threshold.")

    size_match = re.search(
        r"(?:size|larger than|bigger than)[^\d]*([\d,]+(?:\.\d+)?\s*(?:gb|tb)?)", lower
    )
    if size_match:
        parsed_size = _normalize_number(size_match.group(1))
        if parsed_size is not None:
            filt["minSizeGb"] = round(parsed_size, 3)
        else:
            warnings.append("Could not parse size threshold.")

    reclaim = re.search(r"reclaim(?:able)?[^\d]*([\d,]+(?:\.\d+)?)\s*gb", lower)
    if reclaim:
        try:
            filt["minReclaimableGb"] = float(reclaim.group(1).replace(",", ""))
        except ValueError:
            warnings.append("Could not parse reclaimable threshold.")

    contains = re.search(r'(?:name|table)\s*(?:contains|like)\s*"([^"]+)"', text, re.IGNORECASE)
    if not contains:
        contains = re.search(r"(?:name|table)\s*(?:contains|like)\s*([a-zA-Z0-9_\-\.]+)", lower)
    if contains:
        filt["schemaSearch"] = contains.group(1).strip()

    return TranslationResult(filter=filt, warnings=warnings)


def validate_filter_spec(filter_spec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    out: dict[str, Any] = {}
    errors: list[str] = []
    for key, value in filter_spec.items():
        if key not in _ALLOWED_FIELDS:
            errors.append(f"Unsupported filter field: {key}")
            continue
        if key == "activeOnly":
            out[key] = bool(value)
        elif key == "grades":
            if isinstance(value, list):
                grades = [str(v).upper() for v in value if str(v).upper() in _VALID_GRADES]
                if grades:
                    out[key] = sorted(set(grades))
                else:
                    errors.append("grades must include at least one of A-F")
            else:
                errors.append("grades must be an array")
        elif key == "priorities":
            if isinstance(value, list):
                priorities = [str(v).upper() for v in value if str(v).upper() in _VALID_PRIORITIES]
                if priorities:
                    out[key] = sorted(set(priorities))
                else:
                    errors.append("priorities must include HIGH, MED, or LOW")
            else:
                errors.append("priorities must be an array")
        elif key == "needsCompaction":
            out[key] = bool(value)
        elif key == "schemaSearch":
            out[key] = str(value).strip()
        else:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                errors.append(f"{key} must be numeric")
    return out, errors


def build_filter_chips(filter_spec: dict[str, Any]) -> list[str]:
    chips: list[str] = []
    if filter_spec.get("activeOnly") is False:
        chips.append("All tables")
    if filter_spec.get("grades"):
        chips.append(f"Grade: {', '.join(filter_spec['grades'])}")
    if filter_spec.get("priorities"):
        chips.append(f"Priority: {', '.join(filter_spec['priorities'])}")
    if "needsCompaction" in filter_spec:
        chips.append("Needs compaction" if filter_spec["needsCompaction"] else "Healthy only")
    if filter_spec.get("schemaSearch"):
        chips.append(f'Name contains "{filter_spec["schemaSearch"]}"')
    if filter_spec.get("minSizeGb") is not None:
        chips.append(f"Size >= {float(filter_spec['minSizeGb']):g} GB")
    if filter_spec.get("minSmallFiles") is not None:
        chips.append(f"Small files >= {int(float(filter_spec['minSmallFiles']))}")
    if filter_spec.get("minMonthlySavings") is not None:
        chips.append(f"Savings >= ${float(filter_spec['minMonthlySavings']):g}/mo")
    if filter_spec.get("minReclaimableGb") is not None:
        chips.append(f"Reclaimable >= {float(filter_spec['minReclaimableGb']):g} GB")
    return chips


async def translate_with_optional_llm(
    session: Session,
    query: str,
    *,
    use_llm: bool = True,
) -> TranslationResult:
    deterministic = deterministic_translate(query)
    if not use_llm:
        return deterministic

    prompt = (
        "Convert this request into JSON for Cryo TableFilterState.\n"
        "Allowed keys only: activeOnly(boolean), grades(array of A-F), "
        "priorities(array of HIGH|MED|LOW), "
        "needsCompaction(boolean), schemaSearch(string), minSizeGb(number), minSmallFiles(number), "
        "minMonthlySavings(number), minReclaimableGb(number).\n"
        "Return JSON object only.\n"
        f"Request: {query}"
    )
    response, observation = await complete_with_budget_and_observation(
        session,
        prompt,
        invoke=LLMInvokeContext(
            feature="table_filter_translation",
            prompt_name="table_filter_translation",
        ),
        max_tokens=220,
    )
    if response is None:
        deterministic.observation_id = str(observation.id)
        return deterministic

    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(response.content)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", response.content)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = None
    if not isinstance(parsed, dict):
        deterministic.warnings.append("LLM output invalid JSON; deterministic parser used.")
        deterministic.observation_id = str(observation.id)
        return deterministic

    cleaned, errors = validate_filter_spec(parsed)
    if errors:
        deterministic.warnings.append("LLM output partially invalid; deterministic parser used.")
        deterministic.errors.extend(errors)
        deterministic.observation_id = str(observation.id)
        return deterministic
    return TranslationResult(
        filter=cleaned,
        warnings=[],
        errors=[],
        used_llm=True,
        provider=response.provider,
        model=response.model,
        observation_id=str(observation.id),
    )
