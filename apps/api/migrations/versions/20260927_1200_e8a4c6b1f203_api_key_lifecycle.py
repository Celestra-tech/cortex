"""api key lifecycle: display prefix, role, expiry, rotation lineage

Revision ID: e8a4c6b1f203
Revises: d5e1f3a8b92c
Create Date: 2026-09-27 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8a4c6b1f203"
down_revision: str | Sequence[str] | None = "d5e1f3a8b92c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("prefix", sa.String(length=16), nullable=True))
    # Keys minted before roles existed had full access; keep it that way.
    op.add_column(
        "api_keys",
        sa.Column("role", sa.String(length=16), server_default=sa.text("'admin'"), nullable=False),
    )
    op.alter_column("api_keys", "role", server_default=sa.text("'member'"))
    op.create_check_constraint("role", "api_keys", "role IN ('admin', 'member')")
    op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("rotated_from_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_api_keys_rotated_from_id_api_keys"),
        "api_keys",
        "api_keys",
        ["rotated_from_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_api_keys_rotated_from_id_api_keys"), "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "rotated_from_id")
    op.drop_column("api_keys", "expires_at")
    op.drop_constraint(op.f("ck_api_keys_role"), "api_keys", type_="check")
    op.drop_column("api_keys", "role")
    op.drop_column("api_keys", "prefix")
