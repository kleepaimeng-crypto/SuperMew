"""同 Run 持久暂停、一次性恢复接收记录；前向迁移。"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_runs_status", "runs", type_="check")
    op.create_check_constraint(
        "ck_runs_status",
        "runs",
        "status IN ('pending','running','waiting_input','resume_pending','succeeded',"
        "'failed','cancelled')",
    )
    op.drop_constraint("ck_runs_execution_state", "runs", type_="check")
    op.create_check_constraint(
        "ck_runs_execution_state",
        "runs",
        "(status = 'pending' AND owner_token IS NULL AND lease_until IS NULL AND "
        "started_at IS NULL AND finished_at IS NULL AND error_code IS NULL AND "
        "cancel_requested_at IS NULL) OR (status = 'running' AND owner_token IS NOT "
        "NULL AND lease_until IS NOT NULL AND started_at IS NOT NULL AND finished_at "
        "IS NULL AND error_code IS NULL) OR (status IN ('waiting_input',"
        "'resume_pending') AND owner_token IS NULL AND lease_until IS NULL AND "
        "started_at IS NOT NULL AND finished_at IS NULL AND error_code IS NULL AND "
        "cancel_requested_at IS NULL) OR (status IN ('succeeded','failed') AND "
        "((owner_token IS NOT NULL AND lease_until IS NOT NULL) OR (owner_token IS "
        "NULL AND lease_until IS NULL)) AND started_at IS NOT NULL AND finished_at IS "
        "NOT NULL AND ((status = 'succeeded' AND error_code IS NULL AND "
        "cancel_requested_at IS NULL) OR (status = 'failed' AND error_code IS NOT "
        "NULL AND cancel_requested_at IS NULL))) OR (status='cancelled' AND "
        "cancel_requested_at IS NOT NULL AND finished_at IS NOT NULL AND error_code "
        "IS NULL AND ((owner_token IS NULL AND lease_until IS NULL ) OR (owner_token "
        "IS NOT NULL AND lease_until IS NOT NULL AND started_at IS NOT NULL)))",
    )
    op.drop_constraint("ck_messages_role_status", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_role_status",
        "messages",
        "(role = 'user' AND status = 'completed') OR (role = 'assistant' AND status "
        "IN ('pending','waiting_input','completed','failed','cancelled'))",
    )
    op.drop_constraint("ck_run_events_type", "run_events", type_="check")
    op.create_check_constraint(
        "ck_run_events_type",
        "run_events",
        "type IN ('run.created','run.started','message.delta','message.completed',"
        "'run.completed','run.failed','run.cancel_requested','run.cancelled',"
        "'tool.started','tool.completed','tool.failed','hitl.required',"
        "'hitl.accepted','run.resumed')",
    )
    op.execute("""
CREATE TABLE run_checkpoints (
        id UUID NOT NULL,
        run_id UUID NOT NULL,
        document JSONB NOT NULL,
        prompt TEXT NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        token_hash VARCHAR(64),
        resume_key VARCHAR(128),
        input_hash VARCHAR(64),
        answer TEXT,
        accepted_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        CONSTRAINT ck_checkpoint_document CHECK (jsonb_typeof(document) = 'object' AND
    octet_length(document::text) <= 8388608),
        CONSTRAINT ck_checkpoint_prompt CHECK (char_length(prompt) BETWEEN 1 AND 1024),
        CONSTRAINT ck_checkpoint_token CHECK (token_hash IS NULL OR token_hash ~
    '^[0-9a-f]{64}$'),
        CONSTRAINT ck_checkpoint_receipt CHECK ((accepted_at IS NULL AND resume_key IS NULL AND
    input_hash IS NULL AND answer IS NULL) OR (accepted_at IS NOT NULL AND resume_key IS NOT NULL
    AND input_hash ~ '^[0-9a-f]{64}$' AND char_length(answer) BETWEEN 1 AND 1024 AND token_hash IS
    NULL)),
        UNIQUE (run_id),
        FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE RESTRICT
)""")
    op.create_index("ix_run_checkpoints_expires_at", "run_checkpoints", ["expires_at"])
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
                    (OLD.status='running' AND NEW.status IN
                        ('waiting_input','succeeded','failed','cancelled')) OR
                    (OLD.status='waiting_input' AND NEW.status IN
                        ('resume_pending','failed','cancelled')) OR
                    (OLD.status='resume_pending' AND NEW.status IN
                        ('running','failed','cancelled'))
                ) THEN
                    RAISE EXCEPTION 'Invalid Run transition' USING ERRCODE='23514';
                END IF;
                IF ((OLD.status='running' AND NEW.status<>'waiting_input') OR
                    (OLD.status='pending' AND NEW.status='cancelled'))
                    AND (OLD.owner_token,OLD.lease_until,OLD.started_at)
                    IS DISTINCT FROM (NEW.owner_token,NEW.lease_until,NEW.started_at) THEN
                    RAISE EXCEPTION 'Execution ownership is immutable' USING ERRCODE='23514';
                END IF;
                IF OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at
                    THEN
                    RAISE EXCEPTION 'Execution start is immutable' USING ERRCODE='23514';
                END IF;
                IF OLD.status='running' AND NEW.status='waiting_input' AND
                    (OLD.lease_until<=clock_timestamp() OR OLD.cancel_requested_at IS NOT NULL)
                        THEN
                    RAISE EXCEPTION 'Pause requires active owner' USING ERRCODE='23514';
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
        END; $$""")
    op.execute("""
CREATE OR REPLACE FUNCTION minimew_check_run_facts() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            target_id uuid; r runs%ROWTYPE; m messages%ROWTYPE;
            wanted_type text; wanted_message_status text;
            event_count bigint; event_max bigint; cancel_count bigint;
        BEGIN
            IF TG_TABLE_NAME='runs' THEN target_id := NEW.id; ELSE target_id := NEW.run_id; END
                IF;
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
                    SELECT 1 FROM run_events WHERE run_id=target_id AND
                        type='run.cancel_requested'
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
                IF r.status='cancelled' AND r.started_at IS NULL AND EXISTS (
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
        END; $$""")
    op.execute("""
CREATE FUNCTION minimew_guard_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Checkpoint history is immutable' USING ERRCODE='23514';
    END IF;
    IF (OLD.id,OLD.run_id,OLD.document,OLD.prompt,OLD.expires_at) IS DISTINCT FROM
       (NEW.id,NEW.run_id,NEW.document,NEW.prompt,NEW.expires_at) OR
       (OLD.accepted_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Checkpoint and accepted receipt are immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END; $$""")
    op.execute("""
CREATE TRIGGER guard_checkpoint BEFORE UPDATE OR DELETE ON run_checkpoints
FOR EACH ROW EXECUTE FUNCTION minimew_guard_checkpoint()""")
    op.execute("""
CREATE FUNCTION minimew_check_hitl_facts() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_id uuid; r runs%ROWTYPE; c run_checkpoints%ROWTYPE; m messages%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='runs' THEN target_id := NEW.id; ELSE target_id := NEW.run_id; END IF;
    SELECT * INTO STRICT r FROM runs WHERE id=target_id;
    SELECT * INTO c FROM run_checkpoints WHERE run_id=target_id;
    SELECT * INTO STRICT m FROM messages WHERE run_id=target_id AND role='assistant';
    IF (r.status IN ('waiting_input','resume_pending') AND c.id IS NULL) OR
       (c.id IS NULL AND EXISTS(SELECT 1 FROM run_events WHERE run_id=target_id AND
         type IN ('hitl.required','hitl.accepted','run.resumed'))) THEN
        RAISE EXCEPTION 'HITL requires checkpoint' USING ERRCODE='23514';
    END IF;
    IF c.id IS NOT NULL THEN
        IF (SELECT count(*) FROM run_events WHERE run_id=target_id AND type='hitl.required')<>1
           OR NOT EXISTS(SELECT 1 FROM run_events WHERE run_id=target_id AND type='hitl.required'
             AND data->>'checkpoint_id'=c.id::text AND data->>'prompt'=c.prompt
             AND (data->>'expires_at')::timestamptz=c.expires_at) THEN
            RAISE EXCEPTION 'Checkpoint and waiting event must commit together' USING
                ERRCODE='23514';
        END IF;
        IF (c.accepted_at IS NULL AND EXISTS(SELECT 1 FROM run_events WHERE run_id=target_id AND
            type='hitl.accepted')) OR
           (c.accepted_at IS NOT NULL AND (SELECT count(*) FROM run_events WHERE run_id=target_id
               AND
             type='hitl.accepted' AND data->>'checkpoint_id'=c.id::text)<>1) THEN
            RAISE EXCEPTION 'Resume receipt and event must commit together' USING ERRCODE='23514';
        END IF;
    END IF;
    IF (r.status='waiting_input' AND (m.status<>'waiting_input' OR c.accepted_at IS NOT NULL)) OR
       (r.status='resume_pending' AND (m.status<>'pending' OR c.accepted_at IS NULL)) OR
       (r.status NOT IN ('waiting_input') AND m.status='waiting_input') THEN
        RAISE EXCEPTION 'Waiting projection is inconsistent' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END; $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER check_hitl_facts AFTER INSERT OR UPDATE ON runs "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        "minimew_check_hitl_facts()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER check_hitl_facts AFTER INSERT OR UPDATE ON "
        "messages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        "minimew_check_hitl_facts()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER check_hitl_facts AFTER INSERT OR UPDATE ON "
        "run_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        "minimew_check_hitl_facts()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER check_hitl_facts AFTER INSERT OR UPDATE ON "
        "run_checkpoints DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
        "minimew_check_hitl_facts()"
    )


def downgrade():
    raise RuntimeError("Forward-only: preserve checkpoint and resume history")
