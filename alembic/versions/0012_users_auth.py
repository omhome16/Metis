"""0012 — users table + vault ownership (multi-user auth, METIS_AUTH_MODE=users)."""

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.add_column("vaults", sa.Column("owner_id", sa.String(36), nullable=True))
    op.create_index("ix_vaults_owner_id", "vaults", ["owner_id"])
    op.create_foreign_key(
        "fk_vaults_owner_id_users", "vaults", "users", ["owner_id"], ["id"], ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint("fk_vaults_owner_id_users", "vaults", type_="foreignkey")
    op.drop_index("ix_vaults_owner_id", table_name="vaults")
    op.drop_column("vaults", "owner_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
