"""Store per-library rerank settings and immutable Run credential bindings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "rerank_use_bailian", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    for table, prefix in (("knowledge_bases", "kb"), ("runs", "runs")):
        op.add_column(table, sa.Column("rerank_credential_id", UUID()))
        op.create_foreign_key(
            f"fk_{prefix}_rerank_credential_owner",
            table,
            "model_credentials",
            ["rerank_credential_id", "user_id"],
            ["id", "user_id"],
        )
    op.create_check_constraint(
        "ck_kb_rerank_binding",
        "knowledge_bases",
        "NOT rerank_use_bailian OR rerank_credential_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_runs_rerank_binding",
        "runs",
        "COALESCE(model_snapshot_json->'rag'->>'reranker' = 'qwen3-rerank', false)"
        " = (rerank_credential_id IS NOT NULL)",
    )
    op.execute("""CREATE FUNCTION minimew_guard_run_rerank() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.rerank_credential_id IS DISTINCT FROM OLD.rerank_credential_id THEN
        RAISE EXCEPTION 'Run rerank binding is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER guard_run_rerank BEFORE UPDATE ON runs "
        "FOR EACH ROW EXECUTE FUNCTION minimew_guard_run_rerank()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM runs WHERE rerank_credential_id IS NOT NULL)
        OR EXISTS(SELECT 1 FROM knowledge_bases WHERE rerank_credential_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Rerank configuration history prevents downgrade';
      END IF;
    END $$""")
    op.execute("DROP TRIGGER guard_run_rerank ON runs")
    op.execute("DROP FUNCTION minimew_guard_run_rerank()")
    for table, prefix in (("runs", "runs"), ("knowledge_bases", "kb")):
        op.drop_constraint(f"ck_{prefix}_rerank_binding", table, type_="check")
        op.drop_constraint(f"fk_{prefix}_rerank_credential_owner", table, type_="foreignkey")
        op.drop_column(table, "rerank_credential_id")
    op.drop_column("knowledge_bases", "rerank_use_bailian")
