"""memory foundation: conversations, messages, memories

Revision ID: 8b41d6e2c5a7
Revises: 3f9a2c1d7e10
Create Date: 2026-09-27 09:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8b41d6e2c5a7"
down_revision: str | Sequence[str] | None = "3f9a2c1d7e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPED_TABLES = ("conversations", "memories")


def _id() -> sa.Column[object]:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _timestamp(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _deleted_at() -> sa.Column[object]:
    return sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)


def upgrade() -> None:
    op.create_table(
        "conversations",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _deleted_at(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_conversations_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(op.f("ix_conversations_organization_id"), "conversations", ["organization_id"])

    op.create_table(
        "messages",
        _id(),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _timestamp("created_at"),
        sa.CheckConstraint("length(content) > 0", name=op.f("ck_messages_content_not_empty")),
        sa.CheckConstraint("token_count >= 0", name=op.f("ck_messages_token_count_non_negative")),
        sa.CheckConstraint(
            "role IN ('system', 'user', 'assistant', 'tool')",
            name=op.f("ck_messages_message_role"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        "ix_messages_conversation_id_created_at",
        "messages",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "memories",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=1000), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), server_default=sa.text("0.5"), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', summary || ' ' || content)", persisted=True),
            nullable=True,
        ),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _deleted_at(),
        sa.CheckConstraint(
            "importance >= 0 AND importance <= 1", name=op.f("ck_memories_importance_range")
        ),
        sa.CheckConstraint("length(content) > 0", name=op.f("ck_memories_content_not_empty")),
        sa.CheckConstraint(
            "type IN ('episodic', 'semantic', 'procedural', 'preference')",
            name=op.f("ck_memories_memory_type"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_memories_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["messages.id"],
            name=op.f("fk_memories_source_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
    )
    op.create_index(
        "ix_memories_organization_id_created_at",
        "memories",
        ["organization_id", "created_at"],
    )
    op.create_index(op.f("ix_memories_source_message_id"), "memories", ["source_message_id"])
    op.create_index(
        "ix_memories_search_vector", "memories", ["search_vector"], postgresql_using="gin"
    )

    for table in TIMESTAMPED_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in TIMESTAMPED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_set_updated_at ON {table}")

    op.drop_index("ix_memories_search_vector", table_name="memories")
    op.drop_index(op.f("ix_memories_source_message_id"), table_name="memories")
    op.drop_index("ix_memories_organization_id_created_at", table_name="memories")
    op.drop_table("memories")

    op.drop_index("ix_messages_conversation_id_created_at", table_name="messages")
    op.drop_table("messages")

    op.drop_index(op.f("ix_conversations_organization_id"), table_name="conversations")
    op.drop_table("conversations")
