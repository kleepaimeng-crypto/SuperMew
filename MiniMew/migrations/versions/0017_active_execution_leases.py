"""本段绝对活动期限、可续短租约与有界终结宽限；先排空旧 running。"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("LOCK TABLE runs IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM runs WHERE status='running') THEN
            RAISE EXCEPTION 'Drain running Runs using the previous version before upgrade'
                USING ERRCODE='23514';
        END IF;
    END; $$""")
    op.add_column("runs", sa.Column("execution_deadline_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_runs_active_deadline",
        "runs",
        "(status NOT IN ('pending','waiting_input','resume_pending') OR "
        "execution_deadline_at IS NULL) AND (execution_deadline_at IS NULL OR "
        "lease_until <= execution_deadline_at + interval '10 seconds')",
    )
    op.execute("""
CREATE OR REPLACE FUNCTION minimew_guard_execution() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    allowed_seconds double precision;
    cp run_checkpoints%ROWTYPE;
    saved_seconds double precision;
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'pending' OR NEW.cancel_requested_at IS NOT NULL
           OR NEW.execution_deadline_at IS NOT NULL THEN
            RAISE EXCEPTION 'Run must start pending' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF (OLD.id,OLD.thread_id,OLD.user_id,OLD.idempotency_key,OLD.created_at)
       IS DISTINCT FROM (NEW.id,NEW.thread_id,NEW.user_id,NEW.idempotency_key,NEW.created_at) THEN
        RAISE EXCEPTION 'Run identity is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.status IN ('succeeded','failed','cancelled') AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Terminal Run is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.cancel_requested_at IS NOT NULL AND
       NEW.cancel_requested_at IS DISTINCT FROM OLD.cancel_requested_at THEN
        RAISE EXCEPTION 'Cancellation request is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.status<>NEW.status AND NOT (
        (OLD.status='pending' AND NEW.status IN ('running','cancelled')) OR
        (OLD.status='running' AND
         NEW.status IN ('waiting_input','succeeded','failed','cancelled')) OR
        (OLD.status='waiting_input' AND NEW.status IN ('resume_pending','failed','cancelled')) OR
        (OLD.status='resume_pending' AND NEW.status IN ('running','failed','cancelled'))) THEN
        RAISE EXCEPTION 'Invalid Run transition' USING ERRCODE='23514';
    END IF;
    IF OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION 'Execution start is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.status='running' AND NEW.status<>'waiting_input' AND
       NEW.owner_token IS DISTINCT FROM OLD.owner_token THEN
        RAISE EXCEPTION 'Execution owner is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.status='pending' AND NEW.status='cancelled' AND
       (NEW.owner_token,NEW.lease_until,NEW.started_at) IS DISTINCT FROM
       (OLD.owner_token,OLD.lease_until,OLD.started_at) THEN
        RAISE EXCEPTION 'Unstarted cancellation cannot acquire ownership' USING ERRCODE='23514';
    END IF;
    IF OLD.status IN ('pending','resume_pending') AND NEW.status='running' AND
       NEW.execution_deadline_at IS NOT NULL THEN
        RAISE EXCEPTION 'Activate budget after claim' USING ERRCODE='23514';
    END IF;
    IF OLD.status<>'running' AND NEW.status<>'running' AND
       NEW.execution_deadline_at IS DISTINCT FROM OLD.execution_deadline_at THEN
        RAISE EXCEPTION 'Inactive deadline cannot change' USING ERRCODE='23514';
    END IF;
    IF OLD.status='running' AND NEW.status='running' THEN
        IF OLD.execution_deadline_at IS NOT NULL AND
           NEW.execution_deadline_at IS DISTINCT FROM OLD.execution_deadline_at THEN
            RAISE EXCEPTION 'Activity deadline is immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.lease_until IS DISTINCT FROM OLD.lease_until OR
           NEW.execution_deadline_at IS DISTINCT FROM OLD.execution_deadline_at THEN
            IF OLD.lease_until<=clock_timestamp() OR OLD.cancel_requested_at IS NOT NULL
               OR NEW.cancel_requested_at IS NOT NULL THEN
                RAISE EXCEPTION 'Renewal requires current uncancelled owner' USING ERRCODE='23514';
            END IF;
            IF NEW.execution_deadline_at IS NULL OR
               (OLD.execution_deadline_at IS NOT NULL AND
                (NEW.lease_until<OLD.lease_until OR
                 OLD.execution_deadline_at<=clock_timestamp())) THEN
                RAISE EXCEPTION 'Invalid renewal' USING ERRCODE='23514';
            END IF;
        END IF;
        IF OLD.execution_deadline_at IS NULL AND NEW.execution_deadline_at IS NOT NULL THEN
            allowed_seconds := (NEW.model_snapshot_json #>>
                '{answer,configuration,timeout_seconds}')::double precision;
            IF allowed_seconds IS NULL OR allowed_seconds<=0 OR allowed_seconds>300 THEN
                RAISE EXCEPTION 'Invalid frozen activity budget' USING ERRCODE='23514';
            END IF;
            SELECT * INTO cp FROM run_checkpoints WHERE run_id=NEW.id;
            IF FOUND THEN
                IF cp.accepted_at IS NULL OR cp.document->>'version' IS DISTINCT FROM '1' THEN
                    RAISE EXCEPTION 'Invalid resume budget' USING ERRCODE='23514';
                END IF;
                SELECT (entry->>1)::double precision INTO saved_seconds
                    FROM jsonb_array_elements(cp.document->'state'->'v') entry
                    WHERE entry->>0='remaining';
                IF saved_seconds IS NULL OR saved_seconds<=0 OR saved_seconds>300 THEN
                    RAISE EXCEPTION 'Missing resume remainder' USING ERRCODE='23514';
                END IF;
                allowed_seconds := least(allowed_seconds,saved_seconds);
            END IF;
            IF NEW.execution_deadline_at>clock_timestamp()+allowed_seconds*interval '1 second'
               OR NEW.execution_deadline_at<=OLD.started_at THEN
                RAISE EXCEPTION 'Activity exceeds frozen remainder' USING ERRCODE='23514';
            END IF;
        END IF;
    ELSIF OLD.status='running' AND NEW.status='waiting_input' THEN
        IF OLD.lease_until<=clock_timestamp() OR OLD.cancel_requested_at IS NOT NULL
           OR OLD.execution_deadline_at IS NULL OR
              OLD.execution_deadline_at<=clock_timestamp() THEN
            RAISE EXCEPTION 'Pause requires active budget and owner' USING ERRCODE='23514';
        END IF;
    ELSIF OLD.status='running' AND
       (NEW.lease_until,NEW.execution_deadline_at) IS DISTINCT FROM
       (OLD.lease_until,OLD.execution_deadline_at) THEN
        RAISE EXCEPTION 'Terminal ownership history is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.status='running' AND NEW.status IN ('succeeded','failed') THEN
        IF OLD.cancel_requested_at IS NOT NULL THEN
            RAISE EXCEPTION 'Cancellation must be honored' USING ERRCODE='23514';
        END IF;
        IF NEW.error_code IS NOT DISTINCT FROM 'EXECUTION_EXPIRED' THEN
            IF OLD.lease_until>clock_timestamp() THEN
                RAISE EXCEPTION 'Execution has not expired' USING ERRCODE='23514';
            END IF;
        ELSIF OLD.lease_until<=clock_timestamp() THEN
            RAISE EXCEPTION 'Execution owner expired' USING ERRCODE='23514';
        END IF;
        IF NEW.status='succeeded' AND OLD.execution_deadline_at IS NULL THEN
            RAISE EXCEPTION 'Success requires activated execution' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END; $$""")
    op.execute("""
CREATE FUNCTION minimew_guard_activity_write() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r runs%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='run_events' AND
       NEW.type NOT IN ('message.delta','tool.started','tool.completed','tool.failed') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO STRICT r FROM runs WHERE id=NEW.run_id FOR UPDATE;
    IF r.status<>'running' OR r.cancel_requested_at IS NOT NULL OR
       r.lease_until<=clock_timestamp() OR r.execution_deadline_at IS NULL OR
       r.execution_deadline_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'Activity write requires live budget and owner' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END; $$""")
    # 分开定义函数：checkpoint 没有 type 列，不能依赖 SQL 表达式短路访问不存在字段。
    op.execute("""CREATE FUNCTION minimew_guard_checkpoint_insert() RETURNS trigger
LANGUAGE plpgsql AS $$ DECLARE r runs%ROWTYPE; BEGIN
    SELECT * INTO STRICT r FROM runs WHERE id=NEW.run_id FOR UPDATE;
    IF r.status<>'running' OR r.cancel_requested_at IS NOT NULL OR
       r.lease_until<=clock_timestamp() OR r.execution_deadline_at IS NULL OR
       r.execution_deadline_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'Checkpoint requires live budget and owner' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END; $$""")
    op.execute("""CREATE TRIGGER guard_activity_write BEFORE INSERT ON run_events
FOR EACH ROW EXECUTE FUNCTION minimew_guard_activity_write()""")
    op.execute("""CREATE TRIGGER guard_checkpoint_insert BEFORE INSERT ON run_checkpoints
FOR EACH ROW EXECUTE FUNCTION minimew_guard_checkpoint_insert()""")


def downgrade():
    raise RuntimeError("Forward-only: preserve activity deadline and ownership history")
