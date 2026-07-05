"""Create pgvector extension and ANN index for table embeddings."""

from __future__ import annotations

from alembic import op

revision = "20260706_0008"
down_revision = "20260705_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
