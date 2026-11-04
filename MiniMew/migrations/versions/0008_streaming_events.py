"""持久正文分片、失败前缀与追加资格；保留旧生命周期和终态记录。"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_run_events_type", "run_events", type_="check")
    op.create_check_constraint(
        "ck_run_events_type",
        "run_events",
        "type IN ('run.created','run.started','message.delta','message.completed',"
        "'run.completed','run.failed')",
    )
    op.execute("""
        CREATE FUNCTION minimew_guard_delta() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            r runs%ROWTYPE;
            assistant_id uuid;
        BEGIN
            IF NEW.type <> 'message.delta' THEN RETURN NEW; END IF;
            SELECT * INTO STRICT r FROM runs WHERE id=NEW.run_id FOR UPDATE;
            IF r.status <> 'running' OR r.lease_until <= clock_timestamp() THEN
                RAISE EXCEPTION 'Delta requires an active execution' USING ERRCODE='23514';
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
        CREATE TRIGGER guard_delta BEFORE INSERT ON run_events
        FOR EACH ROW EXECUTE FUNCTION minimew_guard_delta()
    """)
    # 0007 的延迟约束继续保证日志连续与终态原子性，这里只增加流式不变量。
    op.execute("""
        CREATE FUNCTION minimew_check_stream_facts() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            target_id uuid;
            r runs%ROWTYPE;
            m messages%ROWTYPE;
            partial_content text;
            delta_count bigint;
        BEGIN
            IF TG_TABLE_NAME='runs' THEN target_id := NEW.id;
            ELSE target_id := NEW.run_id;
            END IF;
            SELECT count(*),string_agg(data->>'content','' ORDER BY sequence)
                INTO delta_count,partial_content FROM run_events
                WHERE run_id=target_id AND type='message.delta';
            IF delta_count=0 THEN RETURN NULL; END IF;
            IF char_length(partial_content) > 100000 THEN
                RAISE EXCEPTION 'Response delta budget exceeded' USING ERRCODE='23514';
            END IF;
            SELECT * INTO STRICT r FROM runs WHERE id=target_id;
            SELECT * INTO STRICT m FROM messages WHERE run_id=target_id AND role='assistant';
            IF r.status='failed' AND m.content IS DISTINCT FROM partial_content THEN
                RAISE EXCEPTION 'Failed message must retain committed deltas'
                    USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END; $$
    """)
    for table in ("runs", "messages", "run_events"):
        op.execute(
            sa.text(f"""
            CREATE CONSTRAINT TRIGGER check_stream_facts AFTER INSERT OR UPDATE ON {table}
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION minimew_check_stream_facts()
        """)
        )


def downgrade():
    raise RuntimeError("Forward-only migration: preserve streamed answers and event history")
