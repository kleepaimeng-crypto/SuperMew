"""明确拒绝已接收恢复记录的 NULL 摘要/正文；保留已执行的 0015。"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_checkpoint_receipt", "run_checkpoints", type_="check")
    op.create_check_constraint(
        "ck_checkpoint_receipt",
        "run_checkpoints",
        "(accepted_at IS NULL AND resume_key IS NULL "
        "AND input_hash IS NULL AND answer IS NULL) OR "
        "(accepted_at IS NOT NULL AND resume_key IS NOT NULL AND "
        "input_hash IS NOT NULL AND answer IS NOT NULL AND "
        "input_hash ~ '^[0-9a-f]{64}$' AND char_length(answer) BETWEEN 1 AND 1024 "
        "AND token_hash IS NULL)",
    )


def downgrade():
    raise RuntimeError("Forward-only: do not weaken accepted resume receipts")
