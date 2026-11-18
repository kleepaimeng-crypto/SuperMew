"""公开过程只追加原Journal；不创建第二套执行状态。"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_run_events_type", "run_events", type_="check")
    op.create_check_constraint(
        "ck_run_events_type",
        "run_events",
        "type IN ('run.created','run.started','message.delta','message.completed',"
        "'run.completed','run.failed','run.cancel_requested','run.cancelled',"
        "'tool.started','tool.completed','tool.failed','hitl.required','hitl.accepted',"
        "'run.resumed','process.step')",
    )
    op.execute("""CREATE FUNCTION minimew_guard_process_event() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE r runs%ROWTYPE; prev run_events%ROWTYPE; source jsonb;
    BEGIN
        IF NEW.type <> 'process.step' THEN RETURN NEW; END IF;
        SELECT * INTO STRICT r FROM runs WHERE id=NEW.run_id FOR UPDATE;
        IF r.status <> 'running' OR r.cancel_requested_at IS NOT NULL
            OR r.lease_until <= clock_timestamp()
            OR r.execution_deadline_at IS NULL OR r.execution_deadline_at <= clock_timestamp() THEN
            RAISE EXCEPTION 'Process requires active execution' USING ERRCODE='23514';
        END IF;
        IF (SELECT count(*) FROM jsonb_object_keys(NEW.data)) <> 11
            OR NEW.data - ARRAY['step_id','parent_step_id','tool_call_id','kind','status',
                'query','summary','count','sources','outcome','elapsed_ms'] <> '{}'::jsonb
            OR NEW.data->>'step_id' IS NULL OR NEW.data->>'step_id' !~ '^[0-9a-f]{32}$'
            OR NEW.data->>'parent_step_id' IS NULL
            OR NEW.data->>'parent_step_id' !~ '^([0-9a-f]{32})?$'
            OR jsonb_typeof(NEW.data->'sources') IS DISTINCT FROM 'array'
            OR jsonb_array_length(NEW.data->'sources') > 6
            OR NEW.data->>'status' IS NULL
            OR NEW.data->>'status' NOT IN ('running','completed','failed') THEN
            RAISE EXCEPTION 'Invalid process payload' USING ERRCODE='23514';
        END IF;
        IF jsonb_typeof(NEW.data->'step_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(NEW.data->'parent_step_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(NEW.data->'tool_call_id') IS DISTINCT FROM 'string'
            OR char_length(NEW.data->>'tool_call_id') > 128
            OR jsonb_typeof(NEW.data->'kind') IS DISTINCT FROM 'string'
            OR NEW.data->>'kind' NOT IN ('model','retrieval','dense','bm25','fusion',
                'merge','rerank','assessment','rewrite','plan','subquestion')
            OR jsonb_typeof(NEW.data->'query') IS DISTINCT FROM 'string'
            OR char_length(NEW.data->>'query') > 1024
            OR jsonb_typeof(NEW.data->'summary') IS DISTINCT FROM 'string'
            OR char_length(NEW.data->>'summary') > 1024
            OR jsonb_typeof(NEW.data->'outcome') IS DISTINCT FROM 'string'
            OR char_length(NEW.data->>'outcome') > 64
            OR jsonb_typeof(NEW.data->'count') IS DISTINCT FROM 'number'
            OR NEW.data->>'count' !~ '^[0-9]+$'
            OR (NEW.data->>'count')::numeric > 1000000
            OR jsonb_typeof(NEW.data->'elapsed_ms') IS DISTINCT FROM 'number'
            OR NEW.data->>'elapsed_ms' !~ '^[0-9]+$'
            OR (NEW.data->>'elapsed_ms')::numeric > 86400000 THEN
            RAISE EXCEPTION 'Invalid process fields' USING ERRCODE='23514';
        END IF;
        IF NEW.data->>'tool_call_id' <> '' AND NOT EXISTS(
            SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND type='tool.started'
            AND data->>'tool_call_id'=NEW.data->>'tool_call_id') THEN
            RAISE EXCEPTION 'Process requires owned ToolCall' USING ERRCODE='23514';
        END IF;
        IF NEW.data->>'status'='running' THEN
            IF EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND type='process.step'
                AND data->>'step_id'=NEW.data->>'step_id')
                OR (NEW.data->>'parent_step_id' <> '' AND NOT EXISTS(
                    SELECT 1 FROM run_events p WHERE p.run_id=NEW.run_id AND p.type='process.step'
                    AND p.data->>'step_id'=NEW.data->>'parent_step_id'
                    AND p.data->>'status'='running'
                    AND p.data->>'tool_call_id'=NEW.data->>'tool_call_id'
                    AND NOT EXISTS(SELECT 1 FROM run_events e WHERE e.run_id=NEW.run_id
                        AND e.type='process.step' AND e.data->>'step_id'=p.data->>'step_id'
                        AND e.data->>'status'<>'running'))) THEN
                RAISE EXCEPTION 'Invalid process start/parent' USING ERRCODE='23514';
            END IF;
        ELSE
            SELECT * INTO prev FROM run_events WHERE run_id=NEW.run_id AND type='process.step'
                AND data->>'step_id'=NEW.data->>'step_id' AND data->>'status'='running';
            IF NOT FOUND OR (prev.data->>'kind',prev.data->>'query',prev.data->>'parent_step_id',
                prev.data->>'tool_call_id') IS DISTINCT FROM
                (NEW.data->>'kind',NEW.data->>'query',NEW.data->>'parent_step_id',
                 NEW.data->>'tool_call_id') OR EXISTS(SELECT 1 FROM run_events WHERE
                    run_id=NEW.run_id AND type='process.step'
                    AND data->>'step_id'=NEW.data->>'step_id' AND data->>'status'<>'running') THEN
                RAISE EXCEPTION 'Invalid process finish' USING ERRCODE='23514';
            END IF;
        END IF;
        FOR source IN SELECT value FROM jsonb_array_elements(NEW.data->'sources') LOOP
            IF NOT EXISTS(SELECT 1 FROM document_versions v WHERE v.user_id=r.user_id
                AND v.id::text=source->>'version_id'
                AND v.document_id::text=source->>'document_id') THEN
                RAISE EXCEPTION 'Process source owner mismatch' USING ERRCODE='23514';
            END IF;
        END LOOP;
        RETURN NEW;
    END $$""")
    op.execute("""CREATE TRIGGER guard_process_event BEFORE INSERT ON run_events
        FOR EACH ROW EXECUTE FUNCTION minimew_guard_process_event()""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM run_events WHERE type='process.step') THEN
        RAISE EXCEPTION 'Process history prevents downgrade'; END IF; END $$""")
    op.execute("DROP TRIGGER guard_process_event ON run_events")
    op.execute("DROP FUNCTION minimew_guard_process_event()")
    op.drop_constraint("ck_run_events_type", "run_events", type_="check")
    op.create_check_constraint(
        "ck_run_events_type",
        "run_events",
        "type IN ('run.created','run.started','message.delta','message.completed',"
        "'run.completed','run.failed','run.cancel_requested','run.cancelled',"
        "'tool.started','tool.completed','tool.failed','hitl.required','hitl.accepted','run.resumed')",
    )
