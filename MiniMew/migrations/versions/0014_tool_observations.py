"""工具公开观测沿用Journal与Outbox，不建立第二套执行状态。"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_run_events_type", "run_events", type_="check")
    op.create_check_constraint(
        "ck_run_events_type",
        "run_events",
        "type IN ('run.created','run.started','message.delta','message.completed',"
        "'run.completed','run.failed','run.cancel_requested','run.cancelled',"
        "'tool.started','tool.completed','tool.failed')",
    )
    op.execute("""
        CREATE FUNCTION minimew_guard_tool_event() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE r runs%ROWTYPE; started run_events%ROWTYPE;
        BEGIN
            IF NEW.type NOT IN ('tool.started','tool.completed','tool.failed') THEN
                RETURN NEW;
            END IF;
            SELECT * INTO STRICT r FROM runs WHERE id=NEW.run_id FOR UPDATE;
            IF r.status <> 'running' OR r.cancel_requested_at IS NOT NULL
                OR r.lease_until <= clock_timestamp() THEN
                RAISE EXCEPTION 'Tool observation requires active execution' USING ERRCODE='23514';
            END IF;
            IF jsonb_typeof(NEW.data->'tool_call_id') IS DISTINCT FROM 'string'
                OR char_length(NEW.data->>'tool_call_id') NOT BETWEEN 1 AND 128
                OR jsonb_typeof(NEW.data->'tool_name') IS DISTINCT FROM 'string'
                OR NEW.data->>'tool_name' !~ '^[a-zA-Z0-9_-]{1,64}$'
                OR (NEW.type <> 'tool.failed' AND
                    NEW.data - 'tool_call_id' - 'tool_name' <> '{}'::jsonb)
                OR (NEW.type = 'tool.failed' AND (
                    jsonb_typeof(NEW.data->'error_code') IS DISTINCT FROM 'string'
                    OR NEW.data - 'tool_call_id' - 'tool_name' - 'error_code' <> '{}'::jsonb)) THEN
                RAISE EXCEPTION 'Invalid tool observation payload' USING ERRCODE='23514';
            END IF;
            SELECT * INTO started FROM run_events WHERE run_id=NEW.run_id AND type='tool.started'
                AND data->>'tool_call_id'=NEW.data->>'tool_call_id';
            IF NEW.type = 'tool.started' THEN
                IF FOUND THEN
                    RAISE EXCEPTION 'Duplicate tool start' USING ERRCODE='23514';
                END IF;
            ELSIF started.event_id IS NULL OR started.data->>'tool_name' <> NEW.data->>'tool_name'
                OR EXISTS (SELECT 1 FROM run_events WHERE run_id=NEW.run_id
                    AND type IN ('tool.completed','tool.failed')
                    AND data->>'tool_call_id'=NEW.data->>'tool_call_id') THEN
                RAISE EXCEPTION 'Invalid tool observation transition' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""
        CREATE TRIGGER guard_tool_event BEFORE INSERT ON run_events
            FOR EACH ROW EXECUTE FUNCTION minimew_guard_tool_event();
    """)


def downgrade():
    raise RuntimeError("Tool observations are durable history; restore a backup instead")
