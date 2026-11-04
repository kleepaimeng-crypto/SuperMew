"""新事件与通知待办同事务；旧 Journal 保持原样。"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_outbox",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error_code", sa.String(32)),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["run_events.event_id"],
            name="fk_event_outbox_event",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 1000000000", name="ck_event_outbox_attempts"),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN ('REDIS_UNAVAILABLE','EVENT_INVALID')",
            name="ck_event_outbox_error",
        ),
    )
    op.create_index(
        "ix_event_outbox_due",
        "event_outbox",
        ["next_attempt_at", "created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )
    # 只约束迁移后新插入的事件，不回填/改写历史事实。
    op.execute("""
        CREATE FUNCTION minimew_require_event_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM event_outbox WHERE event_id=NEW.event_id) THEN
                RAISE EXCEPTION 'New event requires a notification outbox row'
                    USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END; $$
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER require_event_outbox AFTER INSERT ON run_events
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION minimew_require_event_outbox()
    """)
    op.execute("""
        CREATE FUNCTION minimew_guard_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN
                IF OLD.published_at IS NULL THEN
                    RAISE EXCEPTION 'Pending notification must be retained' USING ERRCODE='23514';
                END IF;
                RETURN OLD;
            END IF;
            IF NEW.event_id IS DISTINCT FROM OLD.event_id
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR (OLD.published_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
                RAISE EXCEPTION 'Notification identity or published result is immutable'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""
        CREATE TRIGGER guard_event_outbox BEFORE UPDATE OR DELETE ON event_outbox
        FOR EACH ROW EXECUTE FUNCTION minimew_guard_outbox()
    """)


def downgrade():
    raise RuntimeError("Forward-only migration: retain durable notification work")
