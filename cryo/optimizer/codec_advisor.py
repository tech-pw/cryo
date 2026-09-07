"""Evidence-anchored Parquet compression recommendations.

Cryo does not ship a compression codec and does not claim parity with vendors that do. What it
can do honestly is tell an operator what a *standard* Parquet codec/level change is worth on
their table, gate it on measured value, and keep the compaction, clustering, and compression
line items separate so none of them borrows credit from another.

The expected ranges below come from published measurements, not from modelling
(``docs/automatic-clustering-column-selection.md`` §9.1):

- Uber's fleet migration reported ~39% smaller output moving Snappy to ZSTD across ten large
  Hive tables, ~7% for GZIP to ZSTD, and ~79% for uncompressed data.
- For a table *already* on ZSTD-3 with sensible row groups and layout, the cheap residual from
  level tuning alone is roughly **3-12%**, not the 20-50% headline a lakehouse vendor can quote
  after also compacting 1.45 MB files and re-sorting them.

That last point is the important one commercially. Granica's public datapoint (TPC-DS
``store_sales``, 418 GiB in 301,949 files, ~1.45 MB average) jointly changes file size, row
group size, sort order, and encoding; 266 GiB vs a 359 GiB ZSTD baseline is 25.9% smaller, but
it is not attributable to a codec alone. Cryo should never quote a codec-only number in that
range, and should attribute its own savings per line item.

Estimates here are *priors for triage*, deliberately conservative, and every recommendation
says so. A real number requires sampling the table -- see ``sample_plan`` for the shape of that
probe -- and Cryo reports the measured result once it has one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)

# Economic gate from research note §9.3. A recommendation must clear all three or it is not
# worth a rewrite: relative saving, absolute saving, and positive net value after rewrite cost.
MIN_RELATIVE_SAVING = 0.05
MIN_ABSOLUTE_SAVING_BYTES = 10 * 1024**3  # 10 GB

# Conservative midpoints of published ranges, expressed as expected fractional size reduction.
# Keyed by the *current* codec, normalised lowercase.
_EXPECTED_SAVING_TO_ZSTD: dict[str, float] = {
    "none": 0.79,
    "uncompressed": 0.79,
    "snappy": 0.39,
    "gzip": 0.07,
    "lz4": 0.30,
    "brotli": 0.03,
}

# ZSTD level increases on an already-ZSTD table. Low single digits, occasionally high single
# digits; Athena documents 6-9 as the reasonable trade-off band, and past ~9 the ratio is
# typically flat while write CPU and memory keep climbing.
_ZSTD_LEVEL_SAVING: dict[int, float] = {6: 0.05, 9: 0.08}
_MAX_RECOMMENDED_ZSTD_LEVEL = 9


class CodecAction(StrEnum):
    """What the advisor suggests for a table's compression settings."""

    SWITCH_CODEC = "switch_codec"
    RAISE_LEVEL = "raise_level"
    NONE = "none"


class CodecAbstainReason(StrEnum):
    """Machine-readable reason no compression change is recommended."""

    ALREADY_OPTIMAL = "already_optimal"
    BELOW_RELATIVE_THRESHOLD = "below_relative_threshold"
    BELOW_ABSOLUTE_THRESHOLD = "below_absolute_threshold"
    NEGATIVE_NET_VALUE = "negative_net_value"
    UNKNOWN_TABLE_SIZE = "unknown_table_size"


@dataclass
class CodecRecommendation:
    """A single, economically gated compression change."""

    action: CodecAction
    current_codec: str | None
    recommended_codec: str
    recommended_level: int | None = None
    # Fraction of current bytes expected to disappear. A prior, not a measurement.
    expected_saving_ratio: float = 0.0
    expected_saving_bytes: int = 0
    # Value of that saving over the horizon, minus the cost of rewriting to get it.
    net_value_usd: float = 0.0
    payback_days: float | None = None
    abstained: CodecAbstainReason | None = None
    evidence: str = ""
    # Always true until a sample has actually run; see sample_plan().
    is_estimate: bool = True

    @property
    def recommended(self) -> bool:
        return self.action is not CodecAction.NONE and self.abstained is None


@dataclass
class SampleProbe:
    """One candidate configuration to measure on a sampled slice of the table."""

    codec: str
    level: int | None = None
    row_group_bytes: int | None = None
    note: str = ""


@dataclass
class SamplePlan:
    """A bounded measurement plan that turns the estimate into a real number.

    Research note §9.3: sample 1-5 GiB or 1-3 representative partitions and evaluate no more
    than ~12 candidates, recording output bytes, writer CPU, peak memory, and decode CPU.
    """

    probes: list[SampleProbe] = field(default_factory=list)
    target_sample_bytes: int = 2 * 1024**3

    def __post_init__(self) -> None:
        if len(self.probes) > 12:  # keep the probe budget bounded
            self.probes = self.probes[:12]


def _normalise_codec(codec: str | None) -> str:
    return (codec or "none").strip().lower()


def estimate_codec_change(
    *,
    current_codec: str | None,
    current_level: int | None = None,
    table_size_bytes: int | None,
    storage_cost_per_gb_month: float = 0.023,
    rewrite_cost_per_gb: float = 0.0,
    horizon_days: int = 30,
) -> CodecRecommendation:
    """Estimate the value of a codec/level change, abstaining when it does not pay.

    ``storage_cost_per_gb_month`` defaults to S3 Standard list price; callers with real CUR
    data should pass their measured rate. ``rewrite_cost_per_gb`` should come from completed
    job telemetry -- at its 0.0 default the net value is optimistic, which is why the caller,
    not this function, decides whether to auto-apply anything.
    """
    codec = _normalise_codec(current_codec)

    if not table_size_bytes or table_size_bytes <= 0:
        return CodecRecommendation(
            action=CodecAction.NONE,
            current_codec=current_codec,
            recommended_codec="zstd",
            abstained=CodecAbstainReason.UNKNOWN_TABLE_SIZE,
            evidence="Table size unknown; cannot evaluate whether a rewrite pays for itself.",
        )

    if codec != "zstd":
        ratio = _EXPECTED_SAVING_TO_ZSTD.get(codec, 0.0)
        action = CodecAction.SWITCH_CODEC
        level = None
        evidence = (
            f"Published measurement: {codec} to zstd reduced output by ~{ratio:.0%} "
            "(Uber, ten large production tables). Table-specific result requires a sample."
        )
    else:
        level = current_level if current_level is not None else 3
        if level >= _MAX_RECOMMENDED_ZSTD_LEVEL:
            return CodecRecommendation(
                action=CodecAction.NONE,
                current_codec=current_codec,
                recommended_codec="zstd",
                recommended_level=level,
                abstained=CodecAbstainReason.ALREADY_OPTIMAL,
                evidence=(
                    f"Already at zstd level {level}. Levels past "
                    f"{_MAX_RECOMMENDED_ZSTD_LEVEL} are typically flat on ratio while write "
                    "CPU and memory keep rising; worthwhile only for cold archival data."
                ),
            )
        target_level = 6 if level < 6 else 9
        ratio = _ZSTD_LEVEL_SAVING[target_level]
        action = CodecAction.RAISE_LEVEL
        level = target_level
        evidence = (
            f"zstd level {current_level or 3} to {target_level} is typically a low "
            "single-digit gain; an already-compacted zstd-3 table has roughly 3-12% cheap "
            "residual headroom in total. Not comparable to vendor claims that also change "
            "file size, row-group size, and sort order."
        )

    saving_bytes = int(table_size_bytes * ratio)
    rec = CodecRecommendation(
        action=action,
        current_codec=current_codec,
        recommended_codec="zstd",
        recommended_level=level,
        expected_saving_ratio=ratio,
        expected_saving_bytes=saving_bytes,
        evidence=evidence,
    )

    if ratio < MIN_RELATIVE_SAVING:
        rec.action = CodecAction.NONE
        rec.abstained = CodecAbstainReason.BELOW_RELATIVE_THRESHOLD
        return rec

    if saving_bytes < MIN_ABSOLUTE_SAVING_BYTES:
        rec.action = CodecAction.NONE
        rec.abstained = CodecAbstainReason.BELOW_ABSOLUTE_THRESHOLD
        return rec

    saved_gb = saving_bytes / 1024**3
    monthly_value = saved_gb * storage_cost_per_gb_month
    horizon_value = monthly_value * (horizon_days / 30.0)
    rewrite_cost = (table_size_bytes / 1024**3) * rewrite_cost_per_gb
    rec.net_value_usd = round(horizon_value - rewrite_cost, 4)

    if monthly_value > 0:
        rec.payback_days = round(rewrite_cost / (monthly_value / 30.0), 1) if rewrite_cost else 0.0

    if rec.net_value_usd <= 0:
        rec.action = CodecAction.NONE
        rec.abstained = CodecAbstainReason.NEGATIVE_NET_VALUE

    return rec


def sample_plan(current_codec: str | None, *, include_cold_archival: bool = False) -> SamplePlan:
    """Build the bounded probe set that converts the estimate into a measurement.

    Levels 3/6/9 plus row-group variants, per research note §9.3. Level 19 is included only
    for explicitly cold data: it is slow and memory-hungry to write and usually flat on ratio.
    """
    probes = [
        SampleProbe(codec="zstd", level=3, note="current industry default"),
        SampleProbe(codec="zstd", level=6, note="Athena-recommended trade-off band"),
        SampleProbe(codec="zstd", level=9, note="upper end of the practical band"),
        SampleProbe(
            codec="zstd",
            level=3,
            row_group_bytes=128 * 1024**2,
            note="row-group context often matters more than level",
        ),
    ]
    if _normalise_codec(current_codec) not in ("zstd", "none", "uncompressed"):
        probes.insert(
            0,
            SampleProbe(
                codec=_normalise_codec(current_codec),
                note="control: measure the current setting on the same sample",
            ),
        )
    if include_cold_archival:
        probes.append(
            SampleProbe(codec="zstd", level=19, note="cold archival only; measure write cost")
        )
    return SamplePlan(probes=probes)
