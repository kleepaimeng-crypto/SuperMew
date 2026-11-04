"""持久取消请求、未执行取消与取消终态；保留既有执行事实。"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True)))
    checks = {
        (
            "runs",
            "ck_runs_status",
        ): "status IN ('pending','running','succeeded','failed','cancelled')",
        (
            "runs",
            "ck_runs_execution_state",
        ): "(status = 'pending' AND owner_token IS NULL AND lease_until IS NULL AND "
        "started_at IS NULL AND finished_at IS NULL AND error_code IS NULL "
        "AND cancel_requested_at IS NULL) OR "
        "(status = 'running' AND owner_token IS NOT NULL AND lease_until IS NOT NULL AND "
        "started_at IS NOT NULL AND finished_at IS NULL AND error_code IS NULL) OR "
        "(status IN ('succeeded','failed') AND owner_token IS NOT NULL AND "
        "lease_until IS NOT NULL AND started_at IS NOT NULL AND finished_at IS NOT NULL AND "
        "((status = 'succeeded' AND error_code IS NULL AND cancel_requested_at IS NULL) OR "
        "(status = 'failed' AND error_code IS NOT NULL AND cancel_requested_at IS NULL))) OR "
        "(status='cancelled' AND cancel_requested_at IS NOT NULL AND finished_at IS NOT NULL "
        "AND error_code IS NULL AND ((owner_token IS NULL AND lease_until IS NULL "
        "AND started_at IS NULL) OR (owner_token IS NOT NULL AND lease_until IS NOT NULL "
        "AND started_at IS NOT NULL)))",
        ("messages", "ck_messages_role_status"): "(role = 'user' AND status = 'completed') OR "
        "(role = 'assistant' AND status IN ('pending','completed','failed','cancelled'))",
        (
            "run_events",
            "ck_run_events_type",
        ): "type IN ('run.created','run.started','message.delta','message.completed',"
        "'run.completed','run.failed','run.cancel_requested','run.cancelled')",
    }
    for (table, name), expression in checks.items():
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, expression)
    op.drop_index("uq_run_events_terminal", table_name="run_events")
    op.create_index(
        "uq_run_events_terminal",
        "run_events",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("type IN ('run.completed','run.failed','run.cancelled')"),
    )
    op.create_index(
        "uq_run_events_cancel_requested",
        "run_events",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("type = 'run.cancel_requested'"),
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION minimew_guard_execution() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'pending' OR NEW.cancel_requested_at IS NOT NULL THEN
                    RAISE EXCEPTION 'Run must start pending without cancellation'
                        USING ERRCODE='23514';
                END IF;
            ELSE
                IF (OLD.id,OLD.thread_id,OLD.user_id,OLD.idempotency_key,OLD.created_at)
                    IS DISTINCT FROM
                    (NEW.id,NEW.thread_id,NEW.user_id,NEW.idempotency_key,NEW.created_at) THEN
                    RAISE EXCEPTION 'Run identity is immutable' USING ERRCODE='23514';
                END IF;
                IF OLD.status IN ('succeeded','failed','cancelled')
                    AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'Terminal Run is immutable' USING ERRCODE='23514';
                END IF;
                IF OLD.cancel_requested_at IS NOT NULL AND
                    NEW.cancel_requested_at IS DISTINCT FROM OLD.cancel_requested_at THEN
                    RAISE EXCEPTION 'Cancellation request is immutable' USING ERRCODE='23514';
                END IF;
                IF OLD.status <> NEW.status AND NOT (
                    (OLD.status='pending' AND NEW.status IN ('running','cancelled')) OR
                    (OLD.status='running' AND NEW.status IN ('succeeded','failed','cancelled'))
                ) THEN
                    RAISE EXCEPTION 'Invalid Run transition' USING ERRCODE='23514';
                END IF;
                IF (OLD.status='running' OR (OLD.status='pending' AND NEW.status='cancelled'))
                    AND (OLD.owner_token,OLD.lease_until,OLD.started_at)
                    IS DISTINCT FROM (NEW.owner_token,NEW.lease_until,NEW.started_at) THEN
                    RAISE EXCEPTION 'Execution ownership is immutable' USING ERRCODE='23514';
                END IF;
                IF OLD.status='running' AND NEW.status IN ('succeeded','failed') THEN
                    IF OLD.cancel_requested_at IS NOT NULL THEN
                        RAISE EXCEPTION 'Cancellation must be honored' USING ERRCODE='23514';
                    END IF;
                    IF NEW.error_code IS NOT DISTINCT FROM 'EXECUTION_EXPIRED' THEN
                        IF OLD.lease_until > clock_timestamp() THEN
                            RAISE EXCEPTION 'Execution has not expired' USING ERRCODE='23514';
                        END IF;
                    ELSIF OLD.lease_until <= clock_timestamp() THEN
                        RAISE EXCEPTION 'Execution owner expired' USING ERRCODE='23514';
                    END IF;
                END IF;
                -- cancelled 可由有效 owner 或独立过期终结事务产生，资格由各自条件 UPDATE 裁决。
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION minimew_guard_message() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'Message history is immutable' USING ERRCODE='23514';
            END IF;
            IF (OLD.id,OLD.thread_id,OLD.run_id,OLD.sequence,OLD.role,OLD.created_at)
                IS DISTINCT FROM
                (NEW.id,NEW.thread_id,NEW.run_id,NEW.sequence,NEW.role,NEW.created_at)
                OR (OLD.status IN ('completed','failed','cancelled')
                    AND NEW IS DISTINCT FROM OLD) THEN
                RAISE EXCEPTION 'Completed message and identity are immutable'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION minimew_guard_delta() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE r runs%ROWTYPE; assistant_id uuid;
        BEGIN
            IF NEW.type <> 'message.delta' THEN RETURN NEW; END IF;
            SELECT * INTO STRICT r FROM runs WHERE id=NEW.run_id FOR UPDATE;
            IF r.status <> 'running' OR r.lease_until <= clock_timestamp()
                OR r.cancel_requested_at IS NOT NULL THEN
                RAISE EXCEPTION 'Delta requires an active uncancelled execution'
                    USING ERRCODE='23514';
            END IF;
            SELECT id INTO STRICT assistant_id FROM messages
                WHERE run_id=NEW.run_id AND role='assistant';
            IF jsonb_typeof(NEW.data->'content') IS DISTINCT FROM 'string'
                OR jsonb_typeof(NEW.data->'message_id') IS DISTINCT FROM 'string'
                OR NEW.data->>'message_id' IS DISTINCT FROM assistant_id::text
                OR NEW.data - 'message_id' - 'content' <> '{}'::jsonb
                OR char_length(NEW.data->>'content') NOT BETWEEN 1 AND 4096 THEN
                RAISE EXCEPTION 'Invalid response delta' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION minimew_check_run_facts() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            target_id uuid; r runs%ROWTYPE; m messages%ROWTYPE;
            wanted_type text; wanted_message_status text;
            event_count bigint; event_max bigint; cancel_count bigint;
        BEGIN
            IF TG_TABLE_NAME='runs' THEN target_id := NEW.id; ELSE target_id := NEW.run_id; END IF;
            SELECT * INTO STRICT r FROM runs WHERE id=target_id;
            SELECT count(*),coalesce(max(sequence),0) INTO event_count,event_max
                FROM run_events WHERE run_id=target_id;
            IF event_count <> r.last_event_sequence OR event_max <> r.last_event_sequence THEN
                RAISE EXCEPTION 'Run event cursor is inconsistent' USING ERRCODE='23514';
            END IF;
            SELECT count(*) INTO cancel_count FROM run_events
                WHERE run_id=target_id AND type='run.cancel_requested';
            IF (r.cancel_requested_at IS NULL AND cancel_count<>0)
                OR (r.cancel_requested_at IS NOT NULL AND (cancel_count<>1 OR NOT EXISTS (
                    SELECT 1 FROM run_events WHERE run_id=target_id AND type='run.cancel_requested'
                    AND jsonb_typeof(data->'cancel_requested_at')='string'
                    AND (data->>'cancel_requested_at')::timestamptz=r.cancel_requested_at
                    AND data - 'cancel_requested_at' = '{}'::jsonb
                ))) THEN
                RAISE EXCEPTION 'Cancellation request and event must commit together'
                    USING ERRCODE='23514';
            END IF;
            IF r.status IN ('succeeded','failed','cancelled') THEN
                wanted_type := CASE r.status WHEN 'succeeded' THEN 'run.completed'
                    WHEN 'cancelled' THEN 'run.cancelled' ELSE 'run.failed' END;
                wanted_message_status := CASE r.status WHEN 'succeeded' THEN 'completed'
                    WHEN 'cancelled' THEN 'cancelled' ELSE 'failed' END;
                SELECT * INTO m FROM messages WHERE run_id=target_id AND role='assistant';
                IF NOT FOUND OR m.status <> wanted_message_status THEN
                    RAISE EXCEPTION 'Terminal message is missing' USING ERRCODE='23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM run_events WHERE run_id=target_id AND type=wanted_type
                    AND data->>'status'=r.status
                    AND (r.status<>'failed' OR data->>'error_code'=r.error_code)
                    AND sequence=r.last_event_sequence
                ) OR NOT EXISTS (
                    SELECT 1 FROM run_events WHERE run_id=target_id AND type='message.completed'
                    AND data->>'message_id'=m.id::text AND data->>'status'=m.status
                    AND data->>'content'=m.content AND sequence=r.last_event_sequence-1
                ) THEN
                    RAISE EXCEPTION 'Terminal facts must commit together' USING ERRCODE='23514';
                END IF;
                IF r.status='cancelled' AND r.owner_token IS NULL AND EXISTS (
                    SELECT 1 FROM run_events WHERE run_id=target_id
                        AND type IN ('run.started','message.delta')
                ) THEN
                    RAISE EXCEPTION 'Unstarted cancellation cannot invent execution'
                        USING ERRCODE='23514';
                END IF;
            ELSIF EXISTS (
                SELECT 1 FROM run_events WHERE run_id=target_id
                AND type IN ('message.completed','run.completed','run.failed','run.cancelled')
            ) OR EXISTS (
                SELECT 1 FROM messages WHERE run_id=target_id AND role='assistant'
                AND status IN ('completed','failed','cancelled')
            ) THEN
                RAISE EXCEPTION 'Run has premature terminal facts' USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END; $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION minimew_check_stream_facts() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            target_id uuid; r runs%ROWTYPE; m messages%ROWTYPE;
            partial_content text; delta_count bigint;
        BEGIN
            IF TG_TABLE_NAME='runs' THEN target_id := NEW.id; ELSE target_id := NEW.run_id; END IF;
            SELECT count(*),coalesce(string_agg(data->>'content','' ORDER BY sequence),'')
                INTO delta_count,partial_content FROM run_events
                WHERE run_id=target_id AND type='message.delta';
            IF char_length(partial_content) > 100000 THEN
                RAISE EXCEPTION 'Response delta budget exceeded' USING ERRCODE='23514';
            END IF;
            SELECT * INTO STRICT r FROM runs WHERE id=target_id;
            IF r.status='cancelled' OR (r.status='failed' AND delta_count>0) THEN
                SELECT * INTO STRICT m FROM messages WHERE run_id=target_id AND role='assistant';
                IF m.content IS DISTINCT FROM partial_content THEN
                    RAISE EXCEPTION 'Incomplete message must retain committed deltas'
                        USING ERRCODE='23514';
                END IF;
            END IF;
            RETURN NULL;
        END; $$
    """)


def downgrade():
    raise RuntimeError(
        "Forward-only migration: preserve cancellation requests and terminal history"
    )
