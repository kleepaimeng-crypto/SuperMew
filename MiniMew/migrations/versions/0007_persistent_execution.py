"""Run 最小执行生命周期与事务内终态 Journal。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("owner_token",):
        op.add_column("runs", sa.Column(name, sa.Uuid(), nullable=True))
    for name in ("lease_until", "started_at", "finished_at"):
        op.add_column("runs", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("error_code", sa.String(40), nullable=True))
    op.add_column(
        "runs",
        sa.Column(
            "last_event_sequence", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.drop_constraint("ck_runs_status", "runs", type_="check")
    op.create_check_constraint(
        "ck_runs_status", "runs", "status IN ('pending','running','succeeded','failed')"
    )
    op.create_check_constraint("ck_runs_event_sequence", "runs", "last_event_sequence >= 0")
    op.create_check_constraint(
        "ck_runs_execution_state",
        "runs",
        "(status = 'pending' AND owner_token IS NULL AND lease_until IS NULL AND "
        "started_at IS NULL AND finished_at IS NULL AND error_code IS NULL) OR "
        "(status = 'running' AND owner_token IS NOT NULL AND lease_until IS NOT NULL AND "
        "started_at IS NOT NULL AND finished_at IS NULL AND error_code IS NULL) OR "
        "(status IN ('succeeded','failed') AND owner_token IS NOT NULL AND "
        "lease_until IS NOT NULL AND started_at IS NOT NULL AND finished_at IS NOT NULL AND "
        "((status = 'succeeded' AND error_code IS NULL) OR "
        "(status = 'failed' AND error_code IS NOT NULL)))",
    )
    op.create_index(
        "uq_runs_running_thread",
        "runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_constraint("ck_messages_role_status", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_role_status",
        "messages",
        "(role = 'user' AND status = 'completed') OR "
        "(role = 'assistant' AND status IN ('pending','completed','failed'))",
    )
    op.create_table(
        "run_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id", "thread_id"],
            ["runs.id", "runs.thread_id"],
            name="fk_run_events_run_thread",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_events_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_run_events_sequence"),
        sa.CheckConstraint("schema_version = 1", name="ck_run_events_version"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="ck_run_events_data"),
        sa.CheckConstraint(
            "type IN ('run.created','run.started','message.completed',"
            "'run.completed','run.failed')",
            name="ck_run_events_type",
        ),
    )
    op.create_index(
        "uq_run_events_terminal",
        "run_events",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("type IN ('run.completed','run.failed')"),
    )
    op.create_index(
        "uq_run_events_message_completed",
        "run_events",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("type = 'message.completed'"),
    )
    op.execute("""
        CREATE FUNCTION minimew_guard_execution() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'pending' THEN
                    RAISE EXCEPTION 'Run must start pending' USING ERRCODE='23514';
                END IF;
            ELSE
                IF (OLD.id,OLD.thread_id,OLD.user_id,OLD.idempotency_key,OLD.created_at)
                    IS DISTINCT FROM
                    (NEW.id,NEW.thread_id,NEW.user_id,NEW.idempotency_key,NEW.created_at) THEN
                    RAISE EXCEPTION 'Run identity is immutable' USING ERRCODE='23514';
                END IF;
                IF OLD.status IN ('succeeded','failed') AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'Terminal Run is immutable' USING ERRCODE='23514';
                END IF;
                IF OLD.status <> NEW.status AND NOT (
                    (OLD.status='pending' AND NEW.status='running') OR
                    (OLD.status='running' AND NEW.status IN ('succeeded','failed'))
                ) THEN
                    RAISE EXCEPTION 'Invalid Run transition' USING ERRCODE='23514';
                END IF;
                IF OLD.status='running' AND (OLD.owner_token,OLD.lease_until,OLD.started_at)
                    IS DISTINCT FROM (NEW.owner_token,NEW.lease_until,NEW.started_at) THEN
                    RAISE EXCEPTION 'Execution ownership is immutable' USING ERRCODE='23514';
                END IF;
                IF OLD.status='running' AND NEW.status IN ('succeeded','failed') THEN
                    IF NEW.error_code IS NOT DISTINCT FROM 'EXECUTION_EXPIRED' THEN
                        IF OLD.lease_until > clock_timestamp() THEN
                            RAISE EXCEPTION 'Execution has not expired' USING ERRCODE='23514';
                        END IF;
                    ELSIF OLD.lease_until <= clock_timestamp() THEN
                        RAISE EXCEPTION 'Execution owner expired' USING ERRCODE='23514';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""
        CREATE TRIGGER guard_execution BEFORE INSERT OR UPDATE ON runs
        FOR EACH ROW EXECUTE FUNCTION minimew_guard_execution()
    """)
    op.execute("""
        CREATE FUNCTION minimew_guard_message() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'Message history is immutable' USING ERRCODE='23514';
            END IF;
            IF (OLD.id,OLD.thread_id,OLD.run_id,OLD.sequence,OLD.role,OLD.created_at)
                IS DISTINCT FROM
                (NEW.id,NEW.thread_id,NEW.run_id,NEW.sequence,NEW.role,NEW.created_at)
                OR (OLD.status IN ('completed','failed') AND NEW IS DISTINCT FROM OLD) THEN
                RAISE EXCEPTION 'Completed message and identity are immutable'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""
        CREATE TRIGGER guard_message BEFORE UPDATE OR DELETE ON messages
        FOR EACH ROW EXECUTE FUNCTION minimew_guard_message()
    """)
    op.execute("""
        CREATE FUNCTION minimew_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Run events are append only' USING ERRCODE='23514';
        END; $$
    """)
    op.execute("""
        CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON run_events
        FOR EACH ROW EXECUTE FUNCTION minimew_events_append_only()
    """)
    # 在事务结束时核对，允许消息/事件/Run 在同一事务内按不同 SQL 次序写入。
    op.execute("""
        CREATE FUNCTION minimew_check_run_facts() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            target_id uuid;
            r runs%ROWTYPE;
            m messages%ROWTYPE;
            wanted_type text;
            wanted_message_status text;
            event_count bigint;
            event_max bigint;
        BEGIN
            IF TG_TABLE_NAME='runs' THEN target_id := NEW.id;
            ELSE target_id := NEW.run_id;
            END IF;
            SELECT * INTO STRICT r FROM runs WHERE id=target_id;
            SELECT count(*),coalesce(max(sequence),0) INTO event_count,event_max
                FROM run_events WHERE run_id=target_id;
            IF event_count <> r.last_event_sequence OR event_max <> r.last_event_sequence THEN
                RAISE EXCEPTION 'Run event cursor is inconsistent' USING ERRCODE='23514';
            END IF;
            IF r.status IN ('succeeded','failed') THEN
                wanted_type := CASE WHEN r.status='succeeded'
                    THEN 'run.completed' ELSE 'run.failed' END;
                wanted_message_status := CASE WHEN r.status='succeeded'
                    THEN 'completed' ELSE 'failed' END;
                SELECT * INTO m FROM messages WHERE run_id=target_id AND role='assistant';
                IF NOT FOUND OR m.status <> wanted_message_status THEN
                    RAISE EXCEPTION 'Terminal message is missing' USING ERRCODE='23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM run_events WHERE run_id=target_id AND type=wanted_type
                    AND data->>'status'=r.status
                    AND (r.status='succeeded' OR data->>'error_code'=r.error_code)
                    AND sequence=r.last_event_sequence
                ) OR NOT EXISTS (
                    SELECT 1 FROM run_events WHERE run_id=target_id AND type='message.completed'
                    AND data->>'message_id'=m.id::text AND data->>'status'=m.status
                    AND data->>'content'=m.content AND sequence=r.last_event_sequence-1
                ) THEN
                    RAISE EXCEPTION 'Terminal facts must commit together' USING ERRCODE='23514';
                END IF;
            ELSIF EXISTS (
                SELECT 1 FROM run_events WHERE run_id=target_id
                AND type IN ('message.completed','run.completed','run.failed')
            ) OR EXISTS (
                SELECT 1 FROM messages WHERE run_id=target_id AND role='assistant'
                AND status IN ('completed','failed')
            ) THEN
                RAISE EXCEPTION 'Run has premature terminal facts' USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END; $$
    """)
    for table in ("runs", "messages", "run_events"):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER check_run_facts AFTER INSERT OR UPDATE ON {table}
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION minimew_check_run_facts()
        """)


def downgrade():
    raise RuntimeError("Forward-only migration: preserve Run execution and journal history")
