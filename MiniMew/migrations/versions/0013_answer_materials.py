"""Run的答案依据快照；不改写旧Message和v1 Journal。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_messages_id_run", "messages", ["id", "run_id"])
    op.create_table(
        "answer_materials",
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("materials", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["message_id", "run_id"],
            ["messages.id", "messages.run_id"],
            name="fk_answer_message_run",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("jsonb_typeof(materials) = 'object'", name="ck_answer_materials_object"),
        sa.CheckConstraint(
            "octet_length(materials::text) <= 262144", name="ck_answer_materials_size"
        ),
    )


def downgrade():
    raise RuntimeError(
        "Answer evidence is durable history; restore a backup instead of dropping it"
    )
