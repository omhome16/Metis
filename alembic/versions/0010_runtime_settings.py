"""0010: runtime settings table (self-organizing control panel, P8).

`app_settings` stores JSONB overrides per key; env defaults come from
`app/core/config.py`. Reads never raise, so a missing table falls back to
defaults (ingest/ask keep working pre-migration).
"""

from alembic import op

revision: str = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS app_settings ("
        "key VARCHAR(64) PRIMARY KEY, "
        "value JSONB NOT NULL, "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_settings")
