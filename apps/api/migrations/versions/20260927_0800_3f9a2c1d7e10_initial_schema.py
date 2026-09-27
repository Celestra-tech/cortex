"""initial schema: organizations, users, api_keys, audit_logs

Revision ID: 3f9a2c1d7e10
Revises:
Create Date: 2026-09-27 08:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3f9a2c1d7e10"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPED_TABLES = ("organizations", "users", "api_keys")


def _id() -> sa.Column[object]:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _updated_at() -> sa.Column[object]:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _deleted_at() -> sa.Column[object]:
    return sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$
        """
    )

    op.create_table(
        "organizations",
        _id(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        _created_at(),
        _updated_at(),
        _deleted_at(),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$'",
            name=op.f("ck_organizations_slug_format"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )
    op.create_index(
        "uq_organizations_slug",
        "organizations",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "users",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=32), server_default="member", nullable=False),
        _created_at(),
        _updated_at(),
        _deleted_at(),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')", name=op.f("ck_users_user_role")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_users_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_organization_id"), "users", ["organization_id"])
    op.create_index(
        "uq_users_organization_id_email",
        "users",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "api_keys",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        _deleted_at(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_api_keys_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
    )
    op.create_index(op.f("ix_api_keys_organization_id"), "api_keys", ["organization_id"])

    op.create_table(
        "audit_logs",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource", sa.String(length=255), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_audit_logs_organization_id_organizations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_actor_id"), "audit_logs", ["actor_id"])
    op.create_index(
        "ix_audit_logs_organization_id_created_at",
        "audit_logs",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_metadata",
        "audit_logs",
        ["metadata"],
        postgresql_using="gin",
        postgresql_ops={"metadata": "jsonb_path_ops"},
    )

    for table in TIMESTAMPED_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in TIMESTAMPED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_set_updated_at ON {table}")

    op.drop_index("ix_audit_logs_metadata", table_name="audit_logs")
    op.drop_index("ix_audit_logs_organization_id_created_at", table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor_id"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(op.f("ix_api_keys_organization_id"), table_name="api_keys")
    op.drop_table("api_keys")

    op.drop_index("uq_users_organization_id_email", table_name="users")
    op.drop_index(op.f("ix_users_organization_id"), table_name="users")
    op.drop_table("users")

    op.drop_index("uq_organizations_slug", table_name="organizations")
    op.drop_table("organizations")

    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
