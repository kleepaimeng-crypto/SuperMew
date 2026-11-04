"""最小模型控制面与不可改写的 Run 快照；保留旧无快照历史。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("name", name="uq_model_profiles_name"),
        sa.CheckConstraint("version > 0", name="ck_model_profiles_version"),
        sa.CheckConstraint(
            "jsonb_typeof(configuration) = 'object'", name="ck_model_profiles_config"
        ),
    )
    op.create_table(
        "model_assignments",
        sa.Column("role", sa.String(20), primary_key=True),
        sa.Column("profile_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["model_profiles.id"],
            name="fk_model_assignments_profile",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("role = 'answer'", name="ck_model_assignments_role"),
    )
    op.execute("INSERT INTO model_assignments(role) VALUES ('answer')")
    op.add_column("runs", sa.Column("model_snapshot_json", postgresql.JSONB(), nullable=True))
    op.add_column("runs", sa.Column("model_catalog_hash", sa.String(64), nullable=True))
    op.create_check_constraint(
        "ck_runs_model_snapshot",
        "runs",
        "(model_snapshot_json IS NULL AND model_catalog_hash IS NULL) OR "
        "(model_snapshot_json IS NOT NULL AND model_catalog_hash IS NOT NULL AND "
        "jsonb_typeof(model_snapshot_json) = 'object' AND "
        "model_catalog_hash ~ '^[0-9a-f]{64}$')",
    )
    # 允许保留迁移前的 NULL；禁止任何新 Run 缺少快照或更新已有快照。
    op.execute("""
        CREATE FUNCTION minimew_guard_run_snapshot() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.model_snapshot_json IS NULL OR NEW.model_catalog_hash IS NULL THEN
                    RAISE EXCEPTION 'New Run requires a model snapshot' USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.model_snapshot_json IS DISTINCT FROM OLD.model_snapshot_json
                OR NEW.model_catalog_hash IS DISTINCT FROM OLD.model_catalog_hash THEN
                RAISE EXCEPTION 'Run model snapshot is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER guard_run_snapshot BEFORE INSERT OR UPDATE ON runs
        FOR EACH ROW EXECUTE FUNCTION minimew_guard_run_snapshot()
    """)


def downgrade():
    raise RuntimeError("Forward-only migration: do not discard model profiles or Run snapshots")
