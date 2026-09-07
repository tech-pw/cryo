"""Capability gating for space-filling-curve clustering strategies.

Z-order has been available in Iceberg's ``rewrite_data_files`` for years. Hilbert has not:
`apache/iceberg#16827 <https://github.com/apache/iceberg/pull/16827>`_ merged on 2026-08-25
(commit ``65f6e7493609931f88932b319563a3a8217d6bed``) for the **1.12.0** milestone, and at that
point only on the **Spark 4.1** path -- the 3.5/4.0 backports were not merged. Emitting
``sort-order => 'hilbert(...)'`` against an older runtime fails at procedure-parse time, after
the job has already been submitted and scheduled.

So Hilbert is opt-in *and* version-checked, and it degrades to Z-order rather than failing:
Z-order is the strictly-supported strategy that clusters the same columns, so a fallback still
does useful work. ``resolve_curve_strategy`` returns the reason for any downgrade so the audit
log records why a requested Hilbert run came out as Z-order.

On the benefit side, do not oversell this. Published curve-locality analysis (Moon et al.,
TKDE 2001) reports ~24-47% fewer *contiguous curve runs* for Hilbert on uniform data, but curve
runs are not files: the only public lakehouse file-pruning numbers are ~31% fewer files on
uniform synthetic TPC-H (Iceberg PR #16827) and ~2% on real skewed data (Apache Hudi's Amazon
Reviews benchmark), and Hilbert cost ~15% more rewrite time in the former. Curve choice is
second-order next to picking the right columns and treating skew; see
``docs/automatic-clustering-column-selection.md`` §3.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)

# First Iceberg release carrying Hilbert clustering, and the only Spark line it shipped on.
MIN_ICEBERG_VERSION_FOR_HILBERT = (1, 12, 0)
MIN_SPARK_VERSION_FOR_HILBERT = (4, 1)

_VERSION_RE = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


class CurveStrategy(StrEnum):
    """Multi-column clustering strategies Cryo can render."""

    ZORDER = "zorder"
    HILBERT = "hilbert"


class CurveUnsupportedReason(StrEnum):
    """Machine-readable reason a requested curve was downgraded."""

    DISABLED = "hilbert_disabled"
    UNKNOWN_ICEBERG_VERSION = "unknown_iceberg_version"
    UNKNOWN_SPARK_VERSION = "unknown_spark_version"
    ICEBERG_TOO_OLD = "iceberg_too_old"
    SPARK_TOO_OLD = "spark_too_old"
    ENGINE_UNSUPPORTED = "engine_unsupported"


@dataclass(frozen=True)
class CurveDecision:
    """The strategy that will actually be rendered, and why it differs from the request."""

    strategy: CurveStrategy
    requested: CurveStrategy
    reason: CurveUnsupportedReason | None = None

    @property
    def downgraded(self) -> bool:
        return self.strategy is not self.requested

    def detail(self) -> str | None:
        """Human-readable audit-log line, or None when the request was honored."""
        if not self.downgraded:
            return None
        return (
            f"{self.requested} clustering downgraded to {self.strategy}: {self.reason}"
            f" (Hilbert needs Iceberg >= "
            f"{'.'.join(str(p) for p in MIN_ICEBERG_VERSION_FOR_HILBERT)}"
            f" on Spark >= {'.'.join(str(p) for p in MIN_SPARK_VERSION_FOR_HILBERT)})"
        )


def parse_version(raw: str | None) -> tuple[int, ...] | None:
    """Parse a leading dotted-numeric version, tolerating suffixes like ``1.12.0-amzn-1``.

    Returns None for anything without a leading number, which callers treat as "unknown" --
    never as "new enough".
    """
    if not raw:
        return None
    match = _VERSION_RE.match(str(raw))
    if not match:
        return None
    return tuple(int(p) for p in match.groups() if p is not None)


def _at_least(actual: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    """Compare versions on the components ``minimum`` actually specifies.

    ``(4, 1, 2) >= (4, 1)`` is True; a short actual like ``(4,)`` is zero-padded, so
    ``(4,) >= (4, 1)`` is correctly False.
    """
    padded = actual + (0,) * (len(minimum) - len(actual))
    return padded[: len(minimum)] >= minimum


def hilbert_supported(
    *,
    enabled: bool,
    iceberg_version: str | None,
    spark_version: str | None,
) -> CurveUnsupportedReason | None:
    """Return None when Hilbert can be rendered, else the reason it cannot."""
    if not enabled:
        return CurveUnsupportedReason.DISABLED

    iceberg = parse_version(iceberg_version)
    if iceberg is None:
        return CurveUnsupportedReason.UNKNOWN_ICEBERG_VERSION
    if not _at_least(iceberg, MIN_ICEBERG_VERSION_FOR_HILBERT):
        return CurveUnsupportedReason.ICEBERG_TOO_OLD

    spark = parse_version(spark_version)
    if spark is None:
        return CurveUnsupportedReason.UNKNOWN_SPARK_VERSION
    if not _at_least(spark, MIN_SPARK_VERSION_FOR_HILBERT):
        return CurveUnsupportedReason.SPARK_TOO_OLD

    return None


def resolve_curve_strategy(
    requested: CurveStrategy | str,
    *,
    enabled: bool = False,
    iceberg_version: str | None = None,
    spark_version: str | None = None,
) -> CurveDecision:
    """Resolve a requested curve against runtime capability, downgrading to Z-order if needed."""
    requested_strategy = CurveStrategy(requested)
    if requested_strategy is not CurveStrategy.HILBERT:
        return CurveDecision(strategy=requested_strategy, requested=requested_strategy)

    reason = hilbert_supported(
        enabled=enabled,
        iceberg_version=iceberg_version,
        spark_version=spark_version,
    )
    if reason is None:
        return CurveDecision(strategy=CurveStrategy.HILBERT, requested=CurveStrategy.HILBERT)

    decision = CurveDecision(
        strategy=CurveStrategy.ZORDER,
        requested=CurveStrategy.HILBERT,
        reason=reason,
    )
    logger.info("%s", decision.detail())
    return decision
