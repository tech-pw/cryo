"""Drop engine_impl='rust' from native destinations

Revision ID: 20261026_1210
Revises: 20261026_1200

The Rust cryo-engine is removed, and the API now accepts only engine_impl='go'. A row still
holding 'rust' would fail validation the next time someone edits that destination.

This removes the key rather than rewriting it to 'go'. Routing treats an unset engine_impl
exactly as it treated 'rust': native OPTIMIZE stays off and goes to the SQL fallback, and the
binary that runs expire/orphan is the Go one either way, because the image symlinks
cryo-engine to cryo-engine-go. Rewriting to 'go' would turn native OPTIMIZE on for those
destinations, which is a behavior change this cleanup should not make.
"""

from __future__ import annotations

from alembic import op

revision = "20261026_1210"
down_revision = "20261026_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE cryo.destinations
        SET config = config - 'engine_impl'
        WHERE engine = 'native'
          AND lower(config->>'engine_impl') = 'rust'
        """
    )


def downgrade() -> None:
    # Which rows said 'rust' is not recorded, and the value has no binary to run anymore.
    pass
