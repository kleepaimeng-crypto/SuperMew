"""同一Journal增加有界公开摘要，旧process.step原样保留。"""

from alembic import op

revision = "0021"
down_revision = "0020"
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
        "'run.resumed','process.step','process.detail')",
    )
    op.execute("""CREATE FUNCTION minimew_guard_process_detail() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE r runs%ROWTYPE; item jsonb; value jsonb; section jsonb;
    BEGIN
        IF NEW.type <> 'process.detail' THEN RETURN NEW; END IF;
        SELECT * INTO STRICT r FROM runs WHERE id=NEW.run_id FOR UPDATE;
        IF r.status <> 'running' OR r.cancel_requested_at IS NOT NULL
            OR r.lease_until <= clock_timestamp() OR r.execution_deadline_at IS NULL
            OR r.execution_deadline_at <= clock_timestamp() THEN
            RAISE EXCEPTION 'Process detail requires active execution' USING ERRCODE='23514';
        END IF;
        IF (SELECT count(*) FROM jsonb_object_keys(NEW.data)) <> 8
            OR NEW.data - ARRAY['step_id','stage','phase','purpose','attempt',
                'missing','questions','sources'] <> '{}'::jsonb
            OR jsonb_typeof(NEW.data->'step_id') IS DISTINCT FROM 'string'
            OR NEW.data->>'step_id' !~ '^[0-9a-f]{32}$'
            OR NEW.data->>'stage' IS NULL OR NEW.data->>'stage' NOT IN ('start','result')
            OR NEW.data->>'phase' IS NULL OR NEW.data->>'phase' NOT IN
                ('','initial','aggregate','rewritten','clarified','child')
            OR NEW.data->>'purpose' IS NULL OR NEW.data->>'purpose' NOT IN
                ('','model_decision','tool_selection','answer','answer_repair')
            OR jsonb_typeof(NEW.data->'attempt') IS DISTINCT FROM 'number'
            OR NEW.data->>'attempt' !~ '^[0-2]$'
            OR jsonb_typeof(NEW.data->'missing') IS DISTINCT FROM 'array'
            OR jsonb_array_length(NEW.data->'missing') > 8
            OR jsonb_typeof(NEW.data->'questions') IS DISTINCT FROM 'array'
            OR jsonb_array_length(NEW.data->'questions') > 4
            OR jsonb_typeof(NEW.data->'sources') IS DISTINCT FROM 'array'
            OR jsonb_array_length(NEW.data->'sources') > 6 THEN
            RAISE EXCEPTION 'Invalid process detail payload' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id
            AND type='process.step' AND data->>'step_id'=NEW.data->>'step_id'
            AND data->>'status'='running') OR EXISTS(SELECT 1 FROM run_events
            WHERE run_id=NEW.run_id AND type='process.step'
            AND data->>'step_id'=NEW.data->>'step_id' AND data->>'status'<>'running')
            OR EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND type='process.detail'
            AND data->>'step_id'=NEW.data->>'step_id'
            AND (data->>'stage'=NEW.data->>'stage' OR data->>'stage'='result')) THEN
            RAISE EXCEPTION 'Invalid detail transition' USING ERRCODE='23514';
        END IF;
        FOR value IN SELECT jsonb_array_elements(NEW.data->'missing') LOOP
            IF jsonb_typeof(value) <> 'string'
                OR char_length(value#>>'{}') NOT BETWEEN 1 AND 256 THEN
                RAISE EXCEPTION 'Invalid public gap' USING ERRCODE='23514';
            END IF;
        END LOOP;
        FOR value IN SELECT jsonb_array_elements(NEW.data->'questions') LOOP
            IF jsonb_typeof(value) <> 'string'
                OR char_length(value#>>'{}') NOT BETWEEN 1 AND 1024 THEN
                RAISE EXCEPTION 'Invalid public question' USING ERRCODE='23514';
            END IF;
        END LOOP;
        FOR item IN SELECT jsonb_array_elements(NEW.data->'sources') LOOP
            IF jsonb_typeof(item) IS DISTINCT FROM 'object'
                OR (SELECT count(*) FROM jsonb_object_keys(item)) <> 5
                OR item - ARRAY['document_id','version_id','name','version_number','sections']
                    <> '{}'::jsonb
                OR jsonb_typeof(item->'version_number') IS DISTINCT FROM 'number'
                OR item->>'version_number' !~ '^[0-9]+$' THEN
                RAISE EXCEPTION 'Invalid process source shape' USING ERRCODE='23514';
            END IF;
            IF NOT EXISTS(SELECT 1 FROM document_versions v
                JOIN documents d ON d.id=v.document_id
                JOIN knowledge_bases k ON k.id=d.knowledge_base_id
                JOIN threads t ON t.id=r.thread_id
                WHERE v.user_id=r.user_id AND v.id::text=item->>'version_id'
                AND v.document_id::text=item->>'document_id'
                AND k.project_id IS NOT DISTINCT FROM t.project_id
                AND ((item->>'version_number')::int=0
                    OR v.version_number=(item->>'version_number')::int))
                OR jsonb_typeof(item->'sections') IS DISTINCT FROM 'array'
                OR jsonb_array_length(item->'sections') > 6
                OR jsonb_typeof(item->'name') IS DISTINCT FROM 'string'
                OR char_length(item->>'name') NOT BETWEEN 1 AND 255 THEN
                RAISE EXCEPTION 'Invalid process document source' USING ERRCODE='23514';
            END IF;
            FOR section IN SELECT jsonb_array_elements(item->'sections') LOOP
                IF jsonb_typeof(section) <> 'string'
                    OR char_length(section#>>'{}') NOT BETWEEN 1 AND 200 THEN
                    RAISE EXCEPTION 'Invalid process section' USING ERRCODE='23514';
                END IF;
            END LOOP;
        END LOOP;
        RETURN NEW;
    END $$""")
    op.execute("""CREATE TRIGGER guard_process_detail BEFORE INSERT ON run_events
        FOR EACH ROW EXECUTE FUNCTION minimew_guard_process_detail()""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM run_events WHERE type='process.detail') THEN
        RAISE EXCEPTION 'Process detail history prevents downgrade'; END IF; END $$""")
    op.execute("DROP TRIGGER guard_process_detail ON run_events")
    op.execute("DROP FUNCTION minimew_guard_process_detail()")
    op.drop_constraint("ck_run_events_type", "run_events", type_="check")
    op.create_check_constraint(
        "ck_run_events_type",
        "run_events",
        "type IN ('run.created','run.started','message.delta','message.completed',"
        "'run.completed','run.failed','run.cancel_requested','run.cancelled',"
        "'tool.started','tool.completed','tool.failed','hitl.required','hitl.accepted',"
        "'run.resumed','process.step')",
    )
