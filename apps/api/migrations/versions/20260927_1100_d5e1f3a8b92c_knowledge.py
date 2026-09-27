"""knowledge: pgvector, documents, document_chunks, embeddings, knowledge_queries

Revision ID: d5e1f3a8b92c
Revises: c7d2a9f41b36
Create Date: 2026-09-27 11:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "d5e1f3a8b92c"
down_revision: str | Sequence[str] | None = "c7d2a9f41b36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Needs superuser or a trusted-extension grant on first install; no-op afterwards.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("source", sa.String(length=2048), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("embedding_space", sa.String(length=192), nullable=True),
        sa.Column("ingestion_ms", sa.Integer(), nullable=False),
        sa.Column("embedding_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "ingestion_stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "byte_size >= 0 AND char_count >= 0 AND token_count >= 0 AND chunk_count >= 0",
            name=op.f("ck_documents_counts_non_negative"),
        ),
        sa.CheckConstraint(
            "ingestion_ms >= 0 AND embedding_ms >= 0",
            name=op.f("ck_documents_timings_non_negative"),
        ),
        sa.CheckConstraint("length(title) > 0", name=op.f("ck_documents_title_not_empty")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_documents_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(
        "ix_documents_metadata",
        "documents",
        ["metadata"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"metadata": "jsonb_path_ops"},
    )
    op.create_index(
        "ix_documents_organization_id_created_at",
        "documents",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_documents_organization_id_content_hash",
        "documents",
        ["organization_id", "content_hash"],
        unique=True,
    )
    op.create_table(
        "knowledge_queries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("top_k", sa.SmallInteger(), nullable=False),
        sa.Column(
            "filters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("embedding_space", sa.String(length=192), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("vector_candidates", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("keyword_candidates", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("citation_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("context_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("embedding_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("vector_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("keyword_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "query_embedding_cached", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("top_score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.Column("agreement", sa.Float(), nullable=True),
        sa.Column(
            "results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("completion_id", sa.Uuid(), nullable=True),
        sa.Column("cited", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('hybrid', 'vector', 'keyword')", name=op.f("ck_knowledge_queries_search_mode")
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_knowledge_queries_confidence_range"),
        ),
        sa.CheckConstraint(
            "latency_ms >= 0 AND embedding_ms >= 0 AND vector_ms >= 0 AND keyword_ms >= 0",
            name=op.f("ck_knowledge_queries_timings_non_negative"),
        ),
        sa.CheckConstraint(
            "result_count >= 0 AND vector_candidates >= 0 AND keyword_candidates >= 0 AND citation_count >= 0 AND context_tokens >= 0",
            name=op.f("ck_knowledge_queries_counts_non_negative"),
        ),
        sa.CheckConstraint("top_k >= 1", name=op.f("ck_knowledge_queries_top_k_positive")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_knowledge_queries_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_queries")),
    )
    op.create_index(
        op.f("ix_knowledge_queries_completion_id"),
        "knowledge_queries",
        ["completion_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_queries_organization_id_created_at",
        "knowledge_queries",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=1024), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("embedding", VECTOR(dim=1536), nullable=True),
        sa.Column("embedding_space", sa.String(length=192), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('english', coalesce(section, '')), 'A') || setweight(to_tsvector('english', content), 'B')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(embedding IS NULL) = (embedding_space IS NULL)",
            name=op.f("ck_document_chunks_embedding_space_iff_embedding"),
        ),
        sa.CheckConstraint(
            "char_start >= 0 AND char_end > char_start",
            name=op.f("ck_document_chunks_char_span_valid"),
        ),
        sa.CheckConstraint(
            "chunk_index >= 0", name=op.f("ck_document_chunks_chunk_index_non_negative")
        ),
        sa.CheckConstraint(
            "length(content) > 0", name=op.f("ck_document_chunks_content_not_empty")
        ),
        sa.CheckConstraint(
            "page_start IS NULL OR (page_start >= 1 AND page_end >= page_start)",
            name=op.f("ck_document_chunks_page_span_valid"),
        ),
        sa.CheckConstraint(
            "token_count >= 0", name=op.f("ck_document_chunks_token_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_document_chunks_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
    )
    op.create_index(
        "ix_document_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_document_chunks_organization_id_embedding_space",
        "document_chunks",
        ["organization_id", "embedding_space"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "uq_document_chunks_document_id_chunk_index",
        "document_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )
    op.create_table(
        "embeddings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("dimensions", sa.SmallInteger(), nullable=False),
        sa.Column("vector", VECTOR(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("dimensions > 0", name=op.f("ck_embeddings_dimensions_positive")),
        sa.CheckConstraint(
            "vector_dims(vector) = dimensions", name=op.f("ck_embeddings_vector_matches_dimensions")
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_embeddings_chunk_id_document_chunks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_embeddings")),
    )
    op.create_index(
        "uq_embeddings_chunk_id_space",
        "embeddings",
        ["chunk_id", "provider", "model", "dimensions"],
        unique=True,
    )


def downgrade() -> None:
    # The vector extension is left installed: other schemas in the database may use it.
    op.drop_index("uq_embeddings_chunk_id_space", table_name="embeddings")
    op.drop_table("embeddings")
    op.drop_index("uq_document_chunks_document_id_chunk_index", table_name="document_chunks")
    op.drop_index(
        "ix_document_chunks_search_vector", table_name="document_chunks", postgresql_using="gin"
    )
    op.drop_index(
        "ix_document_chunks_organization_id_embedding_space", table_name="document_chunks"
    )
    op.drop_index(
        "ix_document_chunks_embedding_hnsw",
        table_name="document_chunks",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("document_chunks")
    op.drop_index("ix_knowledge_queries_organization_id_created_at", table_name="knowledge_queries")
    op.drop_index(op.f("ix_knowledge_queries_completion_id"), table_name="knowledge_queries")
    op.drop_table("knowledge_queries")
    op.drop_index("uq_documents_organization_id_content_hash", table_name="documents")
    op.drop_index("ix_documents_organization_id_created_at", table_name="documents")
    op.drop_index(
        "ix_documents_metadata",
        table_name="documents",
        postgresql_using="gin",
        postgresql_ops={"metadata": "jsonb_path_ops"},
    )
    op.drop_table("documents")
