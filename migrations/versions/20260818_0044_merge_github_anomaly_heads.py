"""Merge GitHub 20260818_0042 with gitlab-main 20260817_0043.

GitHub ``main`` grew ``20260817_0041`` (canonical op reorder) and
``20260818_0042`` (shared-S3 anomaly reports) off ``20260813_0040``.
``gitlab-main`` independently grew dark-tables / cost-attribution /
layout-baseline (``20260814_0041`` → ``20260814_0042`` → ``20260817_0043``)
off the same ``0040``. This empty merge revision joins those heads.
"""

from __future__ import annotations


revision = "20260818_0044"
down_revision = ("20260818_0042", "20260817_0043")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
