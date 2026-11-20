"""账户模型与认证加密凭据；Run冻结私有引用和使用偏好。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_credentials",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("user_id", UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("model_id", UUID(), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "user_id", name="uq_model_credentials_owner"),
    )
    op.create_table(
        "account_model_configurations",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("user_id", UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("configuration", JSONB(), nullable=False),
        sa.Column("credential_id", UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "user_id", name="uq_account_models_owner"),
        sa.ForeignKeyConstraint(
            ["credential_id", "user_id"],
            ["model_credentials.id", "model_credentials.user_id"],
            name="fk_account_models_credential_owner",
        ),
        sa.CheckConstraint("revision > 0", name="ck_account_models_revision"),
        sa.CheckConstraint("jsonb_typeof(configuration)='object'", name="ck_account_models_object"),
    )
    op.create_table(
        "account_model_preferences",
        sa.Column("user_id", UUID(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("model_id", UUID()),
        sa.Column(
            "knowledge_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.ForeignKeyConstraint(
            ["model_id", "user_id"],
            ["account_model_configurations.id", "account_model_configurations.user_id"],
            name="fk_account_preferences_model_owner",
        ),
    )
    # 旧Run原本使用部署凭据、允许已部署知识能力；不改旧模型快照或Journal。
    op.add_column("runs", sa.Column("credential_id", UUID(), nullable=True))
    op.add_column(
        "runs",
        sa.Column(
            "knowledge_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.create_foreign_key(
        "fk_runs_credential_owner",
        "runs",
        "model_credentials",
        ["credential_id", "user_id"],
        ["id", "user_id"],
    )
    op.execute("""CREATE FUNCTION minimew_guard_account_run() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.credential_id IS DISTINCT FROM OLD.credential_id
        OR NEW.knowledge_enabled IS DISTINCT FROM OLD.knowledge_enabled THEN
        RAISE EXCEPTION 'Run account binding is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER guard_account_run BEFORE UPDATE ON runs "
        "FOR EACH ROW EXECUTE FUNCTION minimew_guard_account_run()"
    )
    op.execute("""CREATE FUNCTION minimew_guard_model_credential() RETURNS trigger
      LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.id IS DISTINCT FROM OLD.id OR NEW.user_id IS DISTINCT FROM OLD.user_id
        OR NEW.model_id IS DISTINCT FROM OLD.model_id
        OR NEW.encrypted_value IS DISTINCT FROM OLD.encrypted_value
        OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
        RAISE EXCEPTION 'Credential versions are immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER guard_model_credential BEFORE UPDATE ON model_credentials "
        "FOR EACH ROW EXECUTE FUNCTION minimew_guard_model_credential()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM model_credentials)
      OR EXISTS(SELECT 1 FROM runs WHERE NOT knowledge_enabled)
      OR EXISTS(SELECT 1 FROM account_model_preferences WHERE model_id IS NOT NULL
        OR NOT knowledge_enabled)
      OR EXISTS(SELECT 1 FROM runs WHERE
        model_snapshot_json->'answer'->'configuration' ? 'adapter') THEN
      RAISE EXCEPTION 'Account model history prevents downgrade'; END IF; END $$""")
    op.execute("DROP TRIGGER guard_account_run ON runs")
    op.execute("DROP FUNCTION minimew_guard_account_run()")
    op.execute("DROP TRIGGER guard_model_credential ON model_credentials")
    op.execute("DROP FUNCTION minimew_guard_model_credential()")
    op.drop_constraint("fk_runs_credential_owner", "runs", type_="foreignkey")
    op.drop_column("runs", "credential_id")
    op.drop_column("runs", "knowledge_enabled")
    op.drop_table("account_model_preferences")
    op.drop_table("account_model_configurations")
    op.drop_table("model_credentials")
