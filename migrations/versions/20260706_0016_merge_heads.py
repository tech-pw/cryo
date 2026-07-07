"""Merge heads 20260706_0012 and 20260706_0015.

Two parallel subagent runs created independent chains off revision 20260706_0011
(QPP observations) and 20260706_0013 (skillsets + observation + verifier +
CUR/inventory). This empty merge revision joins them so alembic upgrade head
sees a single linear history.
"""

from __future__ import annotations


revision = "20260706_0016"
down_revision = ("20260706_0012", "20260706_0015")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
