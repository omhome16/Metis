"""0009: document extraction_status (P6 OCR) + feedback table (P6).

`documents.extraction_status` tracks how text was obtained: 'ok' (extracted),
'ocr' (pytesseract recovered), 'empty' (zero text — never silent).
`feedback` stores per-message thumbs (-1 | 1) with an optional note.
"""

from alembic import op

revision: str = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE documents "
        "ADD COLUMN IF NOT EXISTS extraction_status VARCHAR(16) NOT NULL DEFAULT 'ok'"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS feedback ("
        "id VARCHAR(36) PRIMARY KEY, "
        "message_id VARCHAR(36) NOT NULL, "
        "rating INT NOT NULL, "
        "note TEXT, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_feedback_message_id ON feedback (message_id)")
    op.execute(
        "ALTER TABLE feedback "
        "ADD CONSTRAINT fk_feedback_message FOREIGN KEY (message_id) "
        "REFERENCES messages (id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS feedback")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS extraction_status")
