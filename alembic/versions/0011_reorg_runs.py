"""0011: reorg_runs (self-organizing audit log + debounce state, P8).

One row per automatic/manual library reorganization: community counts before
and after, summaries made, and docs ingested since the previous run — used both
to debounce auto-reorgs and to power `GET /api/v1/library/reorganizations`.
"""

from alembic import op

revision: str = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS reorg_runs ("
        "id VARCHAR(36) PRIMARY KEY, "
        "run_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "triggered_by VARCHAR(32) NOT NULL DEFAULT 'auto', "
        "docs_since_last INT NOT NULL DEFAULT 0, "
        "communities_before INT NOT NULL DEFAULT 0, "
        "communities_after INT NOT NULL DEFAULT 0, "
        "summaries_made INT NOT NULL DEFAULT 0, "
        "detail JSONB)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_reorg_runs_run_at ON reorg_runs (run_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reorg_runs")
