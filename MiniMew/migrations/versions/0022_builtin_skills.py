"""Run拥有不可变Skill；旧活动协议须先排空，旧终态不补造配置。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM runs WHERE status IN
            ('pending','running','waiting_input','resume_pending')) THEN
            RAISE EXCEPTION 'Complete or cancel existing active Runs before Skill migration';
        END IF;
    END $$""")
    op.add_column(
        "runs", sa.Column("skill_request", postgresql.JSONB(none_as_null=True), nullable=True)
    )
    op.add_column(
        "runs", sa.Column("skill_snapshot", postgresql.JSONB(none_as_null=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_runs_skill_snapshot",
        "runs",
        "(skill_snapshot IS NULL AND skill_request IS NULL AND "
        "status IN ('succeeded','failed','cancelled')) OR "
        "(skill_snapshot IS NOT NULL AND skill_request IS NOT NULL AND "
        "jsonb_typeof(skill_snapshot)='object' AND jsonb_typeof(skill_request)='object')",
    )
    op.execute("""CREATE FUNCTION minimew_guard_skill_snapshot() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='UPDATE' THEN
            IF NEW.skill_request IS DISTINCT FROM OLD.skill_request
                OR NEW.skill_snapshot IS DISTINCT FROM OLD.skill_snapshot THEN
                RAISE EXCEPTION 'Skill snapshot and request are immutable' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.skill_snapshot IS NULL OR NEW.skill_request IS NULL
            OR jsonb_typeof(NEW.skill_snapshot) IS DISTINCT FROM 'object'
            OR jsonb_typeof(NEW.skill_request) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'New Run requires Skill snapshot' USING ERRCODE='23514';
        END IF;
        IF (SELECT count(*) FROM jsonb_object_keys(NEW.skill_snapshot)) <> 9
            OR NEW.skill_snapshot - ARRAY['schema_version','id','version','name','description',
                'instructions','tools','resources','digest'] <> '{}'::jsonb
            OR NEW.skill_snapshot->>'schema_version' IS DISTINCT FROM '1'
            OR NEW.skill_snapshot->>'id' IS NULL
            OR NEW.skill_snapshot->>'id' !~ '^[a-z][a-z0-9-]{0,63}$'
            OR NEW.skill_snapshot->>'digest' IS NULL
            OR NEW.skill_snapshot->>'digest' !~ '^[0-9a-f]{64}$'
            OR jsonb_typeof(NEW.skill_snapshot->'tools') IS DISTINCT FROM 'array'
            OR jsonb_array_length(NEW.skill_snapshot->'tools') > 8
            OR jsonb_typeof(NEW.skill_snapshot->'resources') IS DISTINCT FROM 'array'
            OR jsonb_array_length(NEW.skill_snapshot->'resources') > 4
            OR octet_length(NEW.skill_snapshot::text) > 65536
            OR NEW.skill_request - ARRAY['id','version'] <> '{}'::jsonb THEN
            RAISE EXCEPTION 'Invalid frozen Skill shape' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute("""CREATE TRIGGER guard_skill_snapshot BEFORE INSERT OR UPDATE ON runs
        FOR EACH ROW EXECUTE FUNCTION minimew_guard_skill_snapshot()""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM runs WHERE skill_snapshot IS NOT NULL) THEN
        RAISE EXCEPTION 'Frozen Skill history prevents downgrade'; END IF; END $$""")
    op.execute("DROP TRIGGER guard_skill_snapshot ON runs")
    op.execute("DROP FUNCTION minimew_guard_skill_snapshot()")
    op.drop_constraint("ck_runs_skill_snapshot", "runs", type_="check")
    op.drop_column("runs", "skill_snapshot")
    op.drop_column("runs", "skill_request")
