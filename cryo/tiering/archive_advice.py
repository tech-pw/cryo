"""Pure archive eligibility. Unknown is not cold. No object-store I/O."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import unquote

NO_EVIDENCE_COPY = "No evidence — Auto only"
MIN_EVIDENCE_WINDOW_DAYS = 35
MIN_AVG_FILE_BYTES = 8 * 1024 * 1024
DEFAULT_MIN_QUIET_DAYS = 7
DEFAULT_MIN_IDLE_DAYS = 35
DEFAULT_HOLD_HOT_DAYS = 30
GIR_WINS_BELOW_R = 0.44
HOT_ABOVE_R = 1.0
GIR_BREAKEVEN_R = 0.63
SIA_BREAKEVEN_R = 1.02
SIA_NET_PER_GB = 0.0102
SIA_RETRIEVAL_PER_GB = 0.01
GIR_NET_PER_GB = 0.019
GIR_RETRIEVAL_PER_GB = 0.03
SIA_USD_PER_GB_MONTH = 0.0138
GIR_USD_PER_GB_MONTH = 0.005
SIA_MIN_DURATION_DAYS = 30
GIR_MIN_DURATION_DAYS = 90
TRANSITION_USD_PER_1K = {
    "auto": 0.01,
    "cool": 0.01,
    "archive_instant": 0.02,
    "hot": 0.005,
}

READABLE_CLASSES = frozenset({"STANDARD", "INTELLIGENT_TIERING", "STANDARD_IA", "GLACIER_IR"})
OFFLINE_CLASSES = frozenset(
    {"GLACIER", "DEEP_ARCHIVE", "GLACIER_FLEXIBLE", "GLACIER_FLEXIBLE_RETRIEVAL"}
)
SUGGESTION_LABELS = {
    "hot": "Hot",
    "auto": "Auto",
    "cool": "Cool",
    "archive_instant": "Archive-instant",
    "blocked": "Blocked",
}
_SUGGESTION_RANK = {"blocked": 0, "hot": 0, "auto": 1, "cool": 2, "archive_instant": 3}
_CLASS_FOR_SUGGESTION = {
    "hot": "STANDARD",
    "auto": "INTELLIGENT_TIERING",
    "cool": "STANDARD_IA",
    "archive_instant": "GLACIER_IR",
}
_SUGGESTION_FOR_CLASS = {value: key for key, value in _CLASS_FOR_SUGGESTION.items()}
_EPOCH = date(1970, 1, 1)
_AWS_GUARDRAILS = ("versioning", "object_lock", "kms", "replication")


@dataclass(frozen=True)
class ArchivePolicy:
    name: str
    target_class: str
    min_age_days: int
    scope: str = "source"
    schema_name: str | None = None
    table_name: str | None = None
    min_quiet_days: int = DEFAULT_MIN_QUIET_DAYS
    min_idle_days: int = DEFAULT_MIN_IDLE_DAYS
    mode: str = "advisory"
    excluded_tables: frozenset[str] = frozenset()
    excluded_schemas: frozenset[str] = frozenset()
    excluded_tags: frozenset[str] = frozenset()
    pin_hot: bool = False
    max_bytes_per_run: int | None = None
    hold_hot_days: int = DEFAULT_HOLD_HOT_DAYS
    enabled: bool = True
    offline_target: bool = False


@dataclass(frozen=True)
class PartitionFacts:
    key: str
    partition_value: str | None = None
    partition_path: str | None = None
    partition_key: str | None = None
    transform: str | None = None
    partition_values: Mapping[str, object] | None = None
    base_column: str | None = None
    size_gb: float | None = None
    size_bytes: int | None = None
    file_count: int | None = None
    small_files: int | None = None
    last_write_at: object | None = None
    pin_hot: bool = False
    excluded: bool = False


@dataclass(frozen=True)
class TableContext:
    schema_name: str = ""
    table_name: str = ""
    tags: tuple[str, ...] = ()
    pin_hot: bool = False


@dataclass(frozen=True)
class EvidenceWindow:
    ingestion_healthy: bool | None = None
    window_days: int | None = None
    log_delay_days: int | None = None


@dataclass(frozen=True)
class PartitionReads:
    reads: int | None = None
    bytes_read: int | None = None
    last_read_at: datetime | None = None
    observed: bool = False


UNOBSERVED = PartitionReads()


@dataclass(frozen=True)
class AccessIndex:
    by_path: Mapping[str, PartitionReads]
    by_hive: Mapping[tuple[tuple[str, str], ...], PartitionReads]


@dataclass(frozen=True)
class PartitionAdvice:
    key: str
    partition_value: str | None
    partition_path: str | None
    size_gb: float | None
    size_bytes: int | None
    file_count: int | None
    age_days: int | None
    last_write_at: datetime | None
    last_read_at: datetime | None
    suggestion: str
    suggestion_label: str
    storage_class: str | None
    eligible: bool
    reason_codes: tuple[str, ...]
    evidence_copy: str | None
    break_even_read_share: float | None
    net_saving_usd_month: float | None


@dataclass(frozen=True)
class ArchiveSummary:
    policy_name: str | None
    policy_scope: str | None
    mode: str
    target_class: str | None
    target_label: str | None
    read_evidence: str
    read_evidence_copy: str | None
    projected_savings_usd_month: float | None
    realized_savings_usd_month: float | None
    partition_count: int
    eligible_count: int
    truncated: bool


@dataclass(frozen=True)
class GuardrailResult:
    name: str
    status: str


@dataclass(frozen=True)
class ArchivePlan:
    dry_run: bool
    applied: bool
    writes: bool
    executes: bool
    target_class: str | None
    target_label: str | None
    object_count: int | None
    byte_count: int | None
    request_cost_usd: float | None
    early_delete_exposure_usd: float | None
    guardrails: tuple[GuardrailResult, ...]
    partitions_considered: int
    partitions_selected: int


def evidence_is_trustworthy(window: EvidenceWindow) -> bool:
    if window.ingestion_healthy is not True:
        return False
    if window.window_days is None or window.window_days < MIN_EVIDENCE_WINDOW_DAYS:
        return False
    if window.log_delay_days is None or window.log_delay_days < 0:
        return False
    return window.window_days >= 2 * window.log_delay_days


def policy_from_record(
    *,
    name: str,
    storage_class: str,
    min_age_days: int,
    predicate: Mapping[str, object] | None = None,
    enabled: bool = True,
) -> ArchivePolicy:
    raw = predicate or {}
    scope, schema_name, table_name = _scope(raw)
    exclusions = raw.get("exclusions")
    tables: object = []
    schemas: object = []
    tags: object = []
    if isinstance(exclusions, Mapping):
        tables = exclusions.get("tables") or []
        schemas = exclusions.get("schemas") or []
        tags = exclusions.get("tags") or []
    elif isinstance(exclusions, list):
        tables = exclusions
    mode = raw.get("mode")
    offline = storage_class not in READABLE_CLASSES
    return ArchivePolicy(
        name=name,
        target_class="INTELLIGENT_TIERING" if offline else storage_class,
        min_age_days=min_age_days,
        scope=scope,
        schema_name=schema_name,
        table_name=table_name,
        min_quiet_days=_non_negative_int(raw.get("min_quiet_days"), DEFAULT_MIN_QUIET_DAYS),
        min_idle_days=_non_negative_int(raw.get("min_idle_days"), DEFAULT_MIN_IDLE_DAYS),
        mode=mode if mode in {"advisory", "approval", "auto"} else "advisory",
        excluded_tables=_names(tables),
        excluded_schemas=_names(schemas),
        excluded_tags=_names(tags),
        pin_hot=bool(raw.get("pin_hot")),
        max_bytes_per_run=_optional_non_negative(raw.get("max_bytes_per_run")),
        hold_hot_days=_non_negative_int(raw.get("hold_hot_days"), DEFAULT_HOLD_HOT_DAYS),
        enabled=enabled,
        offline_target=offline,
    )


def resolve_policy(
    policies: Sequence[ArchivePolicy],
    *,
    schema_name: str,
    table_name: str,
) -> ArchivePolicy | None:
    enabled = [item for item in policies if item.enabled]
    table_hits = [
        item
        for item in enabled
        if item.scope == "table" and _table_match(item, schema_name, table_name)
    ]
    schema_hits = [
        item for item in enabled if item.scope == "schema" and item.schema_name == schema_name
    ]
    source_hits = [item for item in enabled if item.scope == "source"]
    chosen = table_hits or schema_hits or source_hits
    if not chosen:
        return None
    return sorted(chosen, key=lambda item: item.name)[0]


def hive_pairs(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for segment in path.split("/"):
        if "=" not in segment:
            continue
        column, _, raw_value = segment.partition("=")
        column = unquote(column)
        if column:
            values[column] = unquote(raw_value)
    return values


def index_access(items: Sequence[tuple[str, PartitionReads]]) -> AccessIndex:
    by_path: dict[str, PartitionReads] = {}
    by_hive: dict[tuple[tuple[str, str], ...], PartitionReads] = {}
    for path, reads in items:
        by_path[path] = _prefer_reads(by_path.get(path), reads)
        pairs = hive_pairs(path)
        if pairs:
            key = tuple(sorted(pairs.items()))
            by_hive[key] = _prefer_reads(by_hive.get(key), reads)
    return AccessIndex(by_path, by_hive)


def match_partition_reads(facts: PartitionFacts, index: AccessIndex) -> PartitionReads:
    for key in (facts.partition_path, facts.partition_key):
        if key and key in index.by_path:
            return index.by_path[key]
    values = facts.partition_values if isinstance(facts.partition_values, Mapping) else None
    if values:
        hive_key = tuple(sorted((str(key), str(value)) for key, value in values.items()))
        if hive_key in index.by_hive:
            return index.by_hive[hive_key]
    return UNOBSERVED


def evaluate_partition(
    facts: PartitionFacts,
    policy: ArchivePolicy | None,
    evidence: EvidenceWindow,
    reads: PartitionReads,
    context: TableContext | None = None,
    *,
    now: datetime | None = None,
) -> PartitionAdvice:
    current = now or datetime.now(UTC)
    table = context or TableContext()
    reasons: list[str] = []
    age = partition_age_days(facts, current)
    write_at = parse_timestamp(facts.last_write_at)
    size_bytes = _size_bytes(facts)
    size_gb = _size_gb(facts, size_bytes)

    exclusion = _exclusion_code(facts, policy, table)
    if exclusion is not None:
        reasons.append(exclusion)
    if age is None or write_at is None:
        reasons.append("no_signal")
    elif policy is not None and age < policy.min_age_days:
        reasons.append("too_young")
    if write_at is not None and policy is not None:
        if _days_since(write_at, current) < policy.min_quiet_days:
            reasons.append("not_quiet")
    compaction = _compaction_reasons(facts, size_bytes)
    reasons.extend(compaction)

    trusted = evidence_is_trustworthy(evidence)
    observed = reads.observed and reads.reads is not None
    if not trusted or not observed:
        reasons.append("no_read_evidence")
    read_share: float | None = None
    recent = False
    timing_unknown = False
    if trusted and observed and reads.reads is not None and reads.reads > 0:
        recency = _is_recent(reads.last_read_at, current, _idle_days(policy))
        if recency is True:
            reasons.append("recent_reads")
            recent = True
        elif recency is None:
            reasons.append("read_timing_unknown")
            timing_unknown = True
        else:
            read_share = _read_share(reads, size_bytes)
            if read_share is None:
                reasons.append("read_volume_unknown")
    elif trusted and observed and reads.reads == 0:
        reasons.append("zero_reads")
        read_share = 0.0

    if policy is None:
        reasons.append("no_policy")
    elif policy.offline_target:
        reasons.append("offline_target_refused")

    structural_block = any(
        code in reasons
        for code in (
            "no_signal",
            "too_young",
            "no_compaction_signal",
            "small_file_backlog",
            "avg_file_below_8mb",
        )
    )
    if structural_block:
        suggestion = "blocked"
    elif exclusion or "not_quiet" in reasons or recent or timing_unknown:
        suggestion = "hot"
    elif "no_read_evidence" in reasons or "read_volume_unknown" in reasons:
        suggestion = "auto"
    elif read_share is not None:
        suggestion = _class_for_read_share(read_share)
    else:
        suggestion = "auto"
    suggestion = _cap(suggestion, _policy_ceiling(policy))
    if "no_read_evidence" in reasons:
        suggestion = _cap(suggestion, "auto")
    storage_class = _CLASS_FOR_SUGGESTION.get(suggestion)
    if storage_class in OFFLINE_CLASSES:
        suggestion = "auto"
        storage_class = "INTELLIGENT_TIERING"
    eligible = suggestion in {"cool", "archive_instant"}
    evidence_copy = NO_EVIDENCE_COPY if "no_read_evidence" in reasons else None
    return PartitionAdvice(
        key=facts.key,
        partition_value=facts.partition_value,
        partition_path=facts.partition_path,
        size_gb=size_gb,
        size_bytes=size_bytes,
        file_count=facts.file_count,
        age_days=age,
        last_write_at=write_at,
        last_read_at=reads.last_read_at,
        suggestion=suggestion,
        suggestion_label=SUGGESTION_LABELS[suggestion],
        storage_class=storage_class,
        eligible=eligible,
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence_copy=evidence_copy,
        break_even_read_share=_break_even(suggestion),
        net_saving_usd_month=_money(_net_saving(suggestion, size_gb, read_share)),
    )


def summarize(
    advices: Sequence[PartitionAdvice],
    policy: ArchivePolicy | None,
    evidence: EvidenceWindow,
    *,
    truncated: bool = False,
) -> ArchiveSummary:
    cold = [item for item in advices if item.eligible]
    measured = [item.net_saving_usd_month for item in cold if item.net_saving_usd_month is not None]
    projected = sum(measured) if cold and len(measured) == len(cold) else None
    trusted = evidence_is_trustworthy(evidence)
    target = None if policy is None or policy.offline_target else policy.target_class
    return ArchiveSummary(
        policy_name=None if policy is None else policy.name,
        policy_scope=None if policy is None else policy.scope,
        mode="advisory" if policy is None else policy.mode,
        target_class=target,
        target_label=SUGGESTION_LABELS.get(_SUGGESTION_FOR_CLASS.get(target or "", "")),
        read_evidence="trusted" if trusted else "missing",
        read_evidence_copy=None if trusted else NO_EVIDENCE_COPY,
        projected_savings_usd_month=_money(projected),
        realized_savings_usd_month=None,
        partition_count=len(advices),
        eligible_count=len(cold),
        truncated=truncated,
    )


def dry_run_plan(
    advices: Sequence[PartitionAdvice],
    *,
    target_class: str | None,
    min_age_days: int | None = None,
    partition_prefix: str | None = None,
    max_bytes: int | None = None,
) -> ArchivePlan:
    normalized, readable = _normalize_target(target_class)
    wanted = _SUGGESTION_FOR_CLASS.get(normalized or "", "auto")
    selected: list[PartitionAdvice] = []
    used_bytes = 0
    for item in advices:
        if item.suggestion != wanted or not _selectable(item, wanted):
            continue
        if min_age_days is not None and (item.age_days is None or item.age_days < min_age_days):
            continue
        if not _prefix_match(item, partition_prefix):
            continue
        if max_bytes is not None:
            if item.size_bytes is None or used_bytes + item.size_bytes > max_bytes:
                continue
            used_bytes += item.size_bytes
        selected.append(item)
    object_count = _sum_known(selected, "file_count")
    byte_count = _sum_known(selected, "size_bytes")
    size_gb = None if byte_count is None else byte_count / (1024**3)
    guardrails = (
        GuardrailResult("no_s3_writes", "pass"),
        GuardrailResult("readable_classes_only", "pass" if readable else "fail"),
        *(GuardrailResult(name, "not_checked") for name in _AWS_GUARDRAILS),
    )
    return ArchivePlan(
        dry_run=True,
        applied=False,
        writes=False,
        executes=False,
        target_class=normalized if readable else None,
        target_label=SUGGESTION_LABELS.get(wanted) if readable else None,
        object_count=object_count if readable else 0,
        byte_count=byte_count if readable else 0,
        request_cost_usd=_money(_request_cost(object_count, wanted)) if readable else None,
        early_delete_exposure_usd=_money(_early_delete(size_gb, wanted)) if readable else None,
        guardrails=guardrails,
        partitions_considered=len(advices),
        partitions_selected=len(selected) if readable else 0,
    )


def partition_age_days(facts: PartitionFacts, now: datetime) -> int | None:
    for value in _candidate_values(facts):
        parsed = _sane(_date_from_transform(facts.transform, value))
        if parsed is not None:
            return max((now.date() - parsed).days, 0)
    if not _looks_temporal(facts):
        write_at = parse_timestamp(facts.last_write_at)
        if write_at is not None:
            return _days_since(write_at, now)
    return None


def parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _policy_ceiling(policy: ArchivePolicy | None) -> str:
    if policy is None or policy.offline_target:
        return "auto"
    return _SUGGESTION_FOR_CLASS.get(policy.target_class, "auto")


def _idle_days(policy: ArchivePolicy | None) -> int:
    return DEFAULT_MIN_IDLE_DAYS if policy is None else policy.min_idle_days


def _exclusion_code(
    facts: PartitionFacts,
    policy: ArchivePolicy | None,
    table: TableContext,
) -> str | None:
    if facts.pin_hot or table.pin_hot or (policy is not None and policy.pin_hot):
        return "pin_hot"
    if facts.excluded:
        return "excluded"
    if policy is None:
        return None
    if table.table_name and table.table_name in policy.excluded_tables:
        return "excluded"
    if table.schema_name and table.schema_name in policy.excluded_schemas:
        return "excluded"
    if policy.excluded_tags and set(table.tags) & policy.excluded_tags:
        return "excluded"
    return None


def _compaction_reasons(facts: PartitionFacts, size_bytes: int | None) -> list[str]:
    if facts.small_files is None or facts.file_count is None or facts.file_count <= 0:
        return ["no_compaction_signal"]
    reasons: list[str] = []
    if facts.small_files > 0:
        reasons.append("small_file_backlog")
    if size_bytes is None:
        reasons.append("no_compaction_signal")
        return reasons
    if size_bytes / facts.file_count < MIN_AVG_FILE_BYTES:
        reasons.append("avg_file_below_8mb")
    return reasons


def _class_for_read_share(read_share: float) -> str:
    if read_share < GIR_WINS_BELOW_R:
        return "archive_instant"
    if read_share <= HOT_ABOVE_R:
        return "cool"
    return "hot"


def _cap(suggestion: str, ceiling: str) -> str:
    if _SUGGESTION_RANK[suggestion] > _SUGGESTION_RANK[ceiling]:
        return ceiling
    return suggestion


def _break_even(suggestion: str) -> float | None:
    if suggestion == "archive_instant":
        return GIR_BREAKEVEN_R
    if suggestion == "cool":
        return SIA_BREAKEVEN_R
    return None


def _net_saving(suggestion: str, size_gb: float | None, read_share: float | None) -> float | None:
    if size_gb is None or read_share is None:
        return None
    if suggestion == "archive_instant":
        return size_gb * (GIR_NET_PER_GB - GIR_RETRIEVAL_PER_GB * read_share)
    if suggestion == "cool":
        return size_gb * (SIA_NET_PER_GB - SIA_RETRIEVAL_PER_GB * read_share)
    return None


def _read_share(reads: PartitionReads, size_bytes: int | None) -> float | None:
    if reads.bytes_read is None or size_bytes is None or size_bytes <= 0:
        return None
    return reads.bytes_read / size_bytes


def _is_recent(last_read: datetime | None, now: datetime, min_idle_days: int) -> bool | None:
    if last_read is None:
        return None
    return _days_since(last_read, now) <= min_idle_days


def _size_bytes(facts: PartitionFacts) -> int | None:
    if facts.size_bytes is not None:
        return facts.size_bytes
    if facts.size_gb is None:
        return None
    return int(facts.size_gb * (1024**3))


def _size_gb(facts: PartitionFacts, size_bytes: int | None) -> float | None:
    if facts.size_gb is not None:
        return facts.size_gb
    if size_bytes is None:
        return None
    return size_bytes / (1024**3)


def _days_since(moment: datetime, now: datetime) -> int:
    return max((now - moment).days, 0)


def _candidate_values(facts: PartitionFacts) -> list[object]:
    values = facts.partition_values if isinstance(facts.partition_values, Mapping) else {}
    ordered: list[object] = []
    if facts.base_column and facts.base_column in values:
        ordered.append(values[facts.base_column])
    if facts.partition_value is not None:
        ordered.append(facts.partition_value)
    ordered.extend(values.values())
    unique: list[object] = []
    for value in ordered:
        if value not in unique:
            unique.append(value)
    return unique


def _looks_temporal(facts: PartitionFacts) -> bool:
    kind = (facts.transform or "").lower()
    if kind in {"day", "month", "year", "hour", "identity"}:
        return True
    return facts.partition_value is not None and _as_date(facts.partition_value) is not None


def _date_from_transform(transform: str | None, value: object) -> date | None:
    kind = (transform or "").lower()
    number = _as_int(value)
    if kind == "day" and number is not None:
        return _EPOCH + timedelta(days=number)
    if kind == "month" and number is not None:
        return date(1970 + number // 12, number % 12 + 1, 1)
    if kind == "year" and number is not None:
        year = number if number >= 1970 else 1970 + number
        return date(year, 1, 1)
    if kind == "hour" and number is not None:
        return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(hours=number)).date()
    return _as_date(value)


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 8 and text.isdigit():
            return _yyyymmdd(text)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    number = _as_int(value)
    if number is not None and 19000101 <= number <= 21001231:
        return _yyyymmdd(f"{number:08d}")
    return None


def _yyyymmdd(text: str) -> date | None:
    try:
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def _sane(value: date | None) -> date | None:
    if value is None or value.year < 1970 or value.year > 2100:
        return None
    return value


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or isinstance(value, float):
        return int(value) if isinstance(value, float) and value.is_integer() else None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        sign = text[1:] if text.startswith("-") else text
        if text and sign.isdigit():
            return int(text)
    return None


def _scope(predicate: Mapping[str, object]) -> tuple[str, str | None, str | None]:
    schema = predicate.get("schema_name")
    table = predicate.get("table_name")
    schema_name = schema if isinstance(schema, str) and schema else None
    table_name = table if isinstance(table, str) and table else None
    scope = predicate.get("scope")
    if scope == "source":
        return "source", None, None
    if scope == "schema":
        return "schema", schema_name, None
    if scope == "table":
        return "table", schema_name, table_name
    if table_name:
        return "table", schema_name, table_name
    if schema_name:
        return "schema", schema_name, None
    return "source", None, None


def _table_match(policy: ArchivePolicy, schema_name: str, table_name: str) -> bool:
    if policy.table_name != table_name:
        return False
    return policy.schema_name is None or policy.schema_name == schema_name


def _names(value: object) -> frozenset[str]:
    if isinstance(value, str) and value:
        return frozenset({value})
    if isinstance(value, list):
        return frozenset(str(item) for item in value if item is not None and str(item))
    return frozenset()


def _optional_non_negative(value: object) -> int | None:
    parsed = _as_int(value)
    if parsed is None or parsed < 0:
        return None
    return parsed


def _non_negative_int(value: object, default: int) -> int:
    parsed = _optional_non_negative(value)
    return default if parsed is None else parsed


def _prefer_reads(current: PartitionReads | None, incoming: PartitionReads) -> PartitionReads:
    if current is None or not current.observed:
        return incoming
    if (incoming.reads or 0) > (current.reads or 0):
        return incoming
    return current


def _normalize_target(target_class: str | None) -> tuple[str | None, bool]:
    if target_class is None:
        return "INTELLIGENT_TIERING", True
    token = target_class.strip()
    aliases = {
        "hot": "STANDARD",
        "auto": "INTELLIGENT_TIERING",
        "cool": "STANDARD_IA",
        "archive_instant": "GLACIER_IR",
        "archive-instant": "GLACIER_IR",
    }
    normalized = aliases.get(token, token)
    if normalized in OFFLINE_CLASSES or normalized not in READABLE_CLASSES:
        return None, False
    return normalized, True


def _selectable(item: PartitionAdvice, wanted: str) -> bool:
    if wanted in {"cool", "archive_instant"}:
        return item.eligible and "no_read_evidence" not in item.reason_codes
    return item.suggestion == wanted


def _prefix_match(item: PartitionAdvice, prefix: str | None) -> bool:
    if not prefix:
        return True
    return any(
        isinstance(value, str) and value.startswith(prefix)
        for value in (item.partition_value, item.key, item.partition_path)
    )


def _sum_known(items: Sequence[PartitionAdvice], field: str) -> int | None:
    values: list[int] = []
    for item in items:
        value = getattr(item, field)
        if value is None:
            return None
        values.append(int(value))
    return sum(values)


def _request_cost(object_count: int | None, suggestion: str) -> float | None:
    if object_count is None:
        return None
    rate = TRANSITION_USD_PER_1K.get(suggestion)
    if rate is None:
        return None
    return object_count / 1000 * rate


def _early_delete(size_gb: float | None, suggestion: str) -> float | None:
    if size_gb is None:
        return None
    if suggestion == "cool":
        return SIA_USD_PER_GB_MONTH * size_gb * (SIA_MIN_DURATION_DAYS / 30)
    if suggestion == "archive_instant":
        return GIR_USD_PER_GB_MONTH * size_gb * (GIR_MIN_DURATION_DAYS / 30)
    return None


def _money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 6)
