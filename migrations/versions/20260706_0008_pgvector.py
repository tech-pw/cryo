"""Create pgvector extension and ANN index for table embeddings.

Graceful-degradation: when the pgvector extension is unavailable on this
Postgres install (e.g. local dev only has pre-built 17/18 binaries and
PG15 lacks matching ones), we skip the ``vector`` bits and let the
schema-level ops proceed. The model layer (``cryo.db.models.TableEmbedding``)
must still compile against a numeric/JSON ``embedding`` column, so we leave
it untouched when pgvector is unavailable.

Downgrade is symmetric: drops HNSW + vec column only when they exist.
"""

from __future__ import annotations

import logging

from alembic import op

revision = "20260706_0008"
down_revision = "20260705_0007"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.env")


def _pgvector_available() -> bool:
    """Probe the running server for the ``vector`` extension."""
    try:
        op.execute(
            "DO $$ BEGIN "
            "  PERFORM 1 FROM pg_available_extensions WHERE name = 'vector'; "
            "EXCEPTION WHEN OTHERS THEN NULL; END $$"
        )
        conn = op.get_bind()
        row = conn.execute(
            op.inline_literal(
                "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
            )
        ).first()
        return row is not None
    except Exception:
        return False


def upgrade() -> None:
    if not _pgvector_available():
        logger.warning(
            "pgvector extension not available on this Postgres install; "
            "skipping vector column + HNSW index. AI semantic search will "
            "fall back to a numpy/JSON path (see cryo.ai.embedding_cache)."
        )
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        ALTER TABLE cryo.table_embeddings
        ADD COLUMN IF NOT EXISTS embedding_vec vector(128)
        """
    )
    op.execute(
        """
        UPDATE cryo.table_embeddings
        SET embedding_vec = embedding::text::vector
        WHERE embedding_vec IS NULL AND embedding IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_table_embeddings_hnsw
        ON cryo.table_embeddings
        USING hnsw (embedding_vec vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS cryo.idx_table_embeddings_hnsw")
    op.execute(
        "ALTER TABLE cryo.table_embeddings DROP COLUMN IF EXISTS embedding_vec"
    )
