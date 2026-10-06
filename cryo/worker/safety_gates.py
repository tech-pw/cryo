"""Quiet-window preflight before metadata-dangerous Iceberg maintenance ops."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from enum import StrEnum

from cryo.domain.enums import OperationType

logger = logging.getLogger(__name__)

DANGEROUS_OPS: frozenset[OperationType] = frozenset(
    {
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.OPTIMIZE_MANIFESTS,
    }
)

DEFAULT_QUIET_PROBES = 2
DEFAULT_QUIET_INTERVAL_SEC = 1.0
ORPHAN_QUIET_PROBES = 3


class TableActivity(StrEnum):
    QUIET = "quiet"
    ACTIVE = "active"
    UNCERTAIN = "uncertain"


TableActivityProbe = Callable[[], TableActivity]


def requires_quiet_window(operation: OperationType) -> bool:
    return operation in DANGEROUS_OPS


def quiet_probe_count(operation: OperationType) -> int:
    if operation is OperationType.REMOVE_ORPHAN_FILES:
        return ORPHAN_QUIET_PROBES
    if operation in DANGEROUS_OPS:
        return DEFAULT_QUIET_PROBES
    return 1


def wait_for_table_quiet(
    probe: TableActivityProbe,
    operation: OperationType,
    *,
    required_probes: int | None = None,
    interval_sec: float = DEFAULT_QUIET_INTERVAL_SEC,
    max_wait_sec: float | None = 120.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """Return True when the table is quiet enough to begin ``operation``.

    Uncertain or active results reset the consecutive-clean counter. Never
    returns True while the latest probe is UNCERTAIN (especially for orphan
    removal).
    """
    need = quiet_probe_count(operation) if required_probes is None else required_probes
    clean = 0
    waited = 0.0
    while clean < need:
        if should_stop is not None and should_stop():
            return False
        activity = probe()
        if activity is TableActivity.QUIET:
            clean += 1
            if clean >= need:
                return True
        else:
            if activity is TableActivity.UNCERTAIN and operation in DANGEROUS_OPS:
                logger.info(
                    "Table activity uncertain before %s; holding for quiet window",
                    operation.value,
                )
            clean = 0
        sleep_fn(interval_sec)
        # interval_sec=0 must still advance waited so max_wait can fire
        waited += interval_sec if interval_sec > 0 else 0.001
        if max_wait_sec is not None and waited >= max_wait_sec:
            return False
    return True
