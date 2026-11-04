"""原子提问预留：Run、Message 与 Thread 消息序号。"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "threads",
        sa.Column("last_sequence", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.create_unique_constraint("uq_threads_id_user", "threads", ["id", "user_id"])
    op.create_check_constraint("ck_threads_sequence", "threads", "last_sequence >= 0")
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["threads.id", "threads.user_id"],
            name="fk_runs_thread_owner",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("thread_id", "idempotency_key", name="uq_runs_thread_key"),
        sa.UniqueConstraint("id", "thread_id", name="uq_runs_id_thread"),
        sa.CheckConstraint("status = 'pending'", name="ck_runs_status"),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "thread_id"],
            ["runs.id", "runs.thread_id"],
            name="fk_messages_run_thread",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("thread_id", "sequence", name="uq_messages_thread_sequence"),
        sa.UniqueConstraint("run_id", "role", name="uq_messages_run_role"),
        sa.CheckConstraint("sequence > 0", name="ck_messages_sequence"),
        sa.CheckConstraint(
            "(role = 'user' AND status = 'completed') OR "
            "(role = 'assistant' AND status = 'pending')",
            name="ck_messages_role_status",
        ),
    )


def downgrade():
    raise RuntimeError(
        "Conversation ledger migration is forward-only; do not discard runs or messages"
    )
