from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import TableStats
from cryo.scanner.detection_tiers import TierInputs, classify_detection_tier


@dataclass(frozen=True)
class CommitDelta:
    table_id: uuid.UUID
    snapshot_id: int
    parent_snapshot_id: int | None
    files_added: int
    files_removed: int
    bytes_added: int
    bytes_removed: int
    delete_files_added: int
    equality_delete_files_added: int = 0
    position_delete_files_added: int = 0
    committed_at: datetime | None = None
    # Removal counterparts, so a publisher that sends real delete-file deltas can bring
    # delete_file_pct back down (a rewrite or expire removes delete files too).
    delete_files_removed: int = 0
    equality_delete_files_removed: int = 0
    position_delete_files_removed: int = 0
    snapshots_added: int = 0

    @property
    def carries_file_deltas(self) -> bool:
        return any(
            (
                self.files_added,
                self.files_removed,
                self.bytes_added,
                self.bytes_removed,
                self.delete_files_added,
                self.delete_files_removed,
                self.equality_delete_files_added,
                self.equality_delete_files_removed,
                self.position_delete_files_added,
                self.position_delete_files_removed,
            )
        )


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _compact_event_history(raw: dict[str, Any], committed_at: datetime, now: datetime) -> list[str]:
    history = raw.get("commit_event_history")
    parsed: list[datetime] = []
    if isinstance(history, list):
        for entry in history:
            if not isinstance(entry, str):
                continue
            try:
                parsed.append(datetime.fromisoformat(entry))
            except ValueError:
                continue
    parsed.append(committed_at)
    lower = now - timedelta(days=7)
    bounded = sorted(
        (ts.astimezone(UTC) for ts in parsed if ts.astimezone(UTC) >= lower),
        reverse=True,
    )
    # Keep the payload bounded to avoid unbounded JSON growth.
    return [ts.isoformat() for ts in bounded[:256]]


def _count_since(history: list[str], now: datetime, window: timedelta) -> int:
    lower = now - window
    count = 0
    for item in history:
        try:
            value = datetime.fromisoformat(item).astimezone(UTC)
        except ValueError:
            continue
        if value >= lower:
            count += 1
    return count


def apply_commit_delta(
    session: Session, delta: CommitDelta, *, now: datetime | None = None
) -> bool:
    """Apply a commit delta to TableStats with idempotent snapshot watermarking.

    Returns ``False`` when this event is stale (``snapshot_id`` already applied) or when
    the table has no scanner baseline yet.

    ``scan_at`` is deliberately left alone: it is when the table was last *measured*. A
    commit event moves the watermark and ``last_snapshot_committed_at`` but recomputes no
    grade, score, small-file count or reclaimable bytes, so advancing ``scan_at`` made a hot
    table look freshly scanned with days-old health and passed every staleness check
    (``max_stats_age``, ``stale_stats``) indefinitely (issue #188). The commit is recorded in
    ``raw.commits_since_scan`` instead, and the maintenance-eval enqueue that follows the
    event is what triggers a re-score.
    """
    stats = session.get(TableStats, delta.table_id)
    if stats is None:
        return False

    current_watermark = stats.snapshot_watermark
    if current_watermark is not None and delta.snapshot_id <= current_watermark:
        return False

    observed_now = (now or datetime.now(UTC)).astimezone(UTC)
    committed_at = (delta.committed_at or observed_now).astimezone(UTC)

    stats.snapshot_watermark = delta.snapshot_id
    stats.last_snapshot_committed_at = committed_at
    stats.total_files = max(
        0, _safe_int(stats.total_files) + delta.files_added - delta.files_removed
    )

    existing_total_bytes = int((stats.total_size_gb or 0.0) * 1024**3)
    updated_total_bytes = max(0, existing_total_bytes + delta.bytes_added - delta.bytes_removed)
    stats.total_size_gb = updated_total_bytes / 1024**3

    existing_delete_files = _safe_int(stats.total_delete_files)
    stats.total_delete_files = max(
        0, existing_delete_files + delta.delete_files_added - delta.delete_files_removed
    )

    stats.equality_delete_files = max(
        0,
        _safe_int(stats.equality_delete_files)
        + delta.equality_delete_files_added
        - delta.equality_delete_files_removed,
    )
    stats.position_delete_files = max(
        0,
        _safe_int(stats.position_delete_files)
        + delta.position_delete_files_added
        - delta.position_delete_files_removed,
    )
    if delta.snapshots_added > 0:
        stats.total_snapshots = _safe_int(stats.total_snapshots) + delta.snapshots_added

    if stats.total_files > 0:
        stats.delete_file_pct = min(
            100.0, max(0.0, (stats.total_delete_files / stats.total_files) * 100.0)
        )
    else:
        stats.delete_file_pct = 0.0

    raw = dict(stats.raw or {})
    history = _compact_event_history(raw, committed_at, observed_now)
    commits_24h = _count_since(history, observed_now, timedelta(hours=24))
    commits_7d = _count_since(history, observed_now, timedelta(days=7))

    decision = classify_detection_tier(
        TierInputs(
            commits_24h=commits_24h,
            commits_7d=commits_7d,
            query_count_7d=max(0, _safe_int(stats.query_count_30d) // 4),
            small_files_count=max(0, _safe_int(stats.small_files_count)),
            delete_file_pct=float(stats.delete_file_pct or 0.0),
            last_scan_at=stats.scan_at,
        ),
        now=observed_now,
    )
    stats.detection_tier = decision.tier.value

    smart_payload = dict(raw.get("smart_maintenance") or {})
    smart_payload.update(
        {
            "last_commit_snapshot_id": delta.snapshot_id,
            "last_commit_parent_snapshot_id": delta.parent_snapshot_id,
            "last_commit_seen_at": observed_now.isoformat(),
            "last_commit_committed_at": committed_at.isoformat(),
            "commits_24h": commits_24h,
            "commits_7d": commits_7d,
            "detection_tier_score": decision.score,
            "detection_tier_reason": decision.reason,
            "last_delta": {
                "files_added": delta.files_added,
                "files_removed": delta.files_removed,
                "bytes_added": delta.bytes_added,
                "bytes_removed": delta.bytes_removed,
                "delete_files_added": delta.delete_files_added,
                "equality_delete_files_added": delta.equality_delete_files_added,
                "position_delete_files_added": delta.position_delete_files_added,
                "delete_files_removed": delta.delete_files_removed,
                "equality_delete_files_removed": delta.equality_delete_files_removed,
                "position_delete_files_removed": delta.position_delete_files_removed,
                "snapshots_added": delta.snapshots_added,
            },
        }
    )
    raw["smart_maintenance"] = smart_payload
    raw["commit_event_history"] = history
    raw["last_snapshot_committed_at"] = committed_at.isoformat()
    # Health fields (grade, score, small files, reclaimable bytes) are as of scan_at; this
    # counts commits applied on top of that measurement. A full scan rewrites raw and resets it.
    raw["commits_since_scan"] = _safe_int(raw.get("commits_since_scan")) + 1
    if not delta.carries_file_deltas:
        raw["watermark_only_commits_since_scan"] = (
            _safe_int(raw.get("watermark_only_commits_since_scan")) + 1
        )
    stats.raw = raw
    return True
