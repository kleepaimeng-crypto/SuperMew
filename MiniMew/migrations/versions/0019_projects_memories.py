"""Projects, document withdrawal and frozen manual memories."""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE projects (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id UUID NOT NULL REFERENCES users(id),
        name VARCHAR(100) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_projects_owner UNIQUE(id,user_id))""")
    op.execute("""CREATE TABLE project_memories (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(), project_id UUID NOT NULL, user_id UUID
        NOT NULL,
        content TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, enabled BOOLEAN NOT NULL
        DEFAULT true,
        deleted_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT fk_project_memories_owner FOREIGN KEY(project_id,user_id)
        REFERENCES projects(id,user_id))""")
    op.execute("ALTER TABLE threads ADD COLUMN project_id UUID")
    op.execute(
        "ALTER TABLE threads ADD CONSTRAINT fk_threads_project_owner FOREIGN "
        "KEY(project_id,user_id) REFERENCES projects(id,user_id)"
    )
    op.execute("ALTER TABLE knowledge_bases ADD COLUMN project_id UUID")
    op.execute(
        "ALTER TABLE knowledge_bases ADD CONSTRAINT fk_kb_project_owner FOREIGN "
        "KEY(project_id,user_id) REFERENCES projects(id,user_id)"
    )
    op.execute("ALTER TABLE knowledge_bases DROP CONSTRAINT uq_knowledge_bases_user")
    op.execute(
        "CREATE UNIQUE INDEX uq_knowledge_bases_personal ON knowledge_bases(user_id) WHERE "
        "project_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_knowledge_bases_project ON knowledge_bases(project_id) WHERE "
        "project_id IS NOT NULL"
    )
    op.execute("ALTER TABLE documents ADD COLUMN deleted_at TIMESTAMPTZ")
    op.execute("ALTER TABLE runs ADD COLUMN memory_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("""CREATE FUNCTION minimew_guard_run_memory() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.memory_snapshot IS DISTINCT FROM OLD.memory_snapshot THEN
            RAISE EXCEPTION 'Run memory snapshot is immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW; END $$""")
    op.execute("""CREATE TRIGGER guard_run_memory BEFORE UPDATE ON runs
        FOR EACH ROW EXECUTE FUNCTION minimew_guard_run_memory()""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM projects)
        OR EXISTS(SELECT 1 FROM documents WHERE deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Project or withdrawn document data prevents downgrade';
        END IF; END $$""")
    op.execute("DROP TRIGGER guard_run_memory ON runs")
    op.execute("DROP FUNCTION minimew_guard_run_memory()")
    op.drop_column("runs", "memory_snapshot")
    op.drop_column("documents", "deleted_at")
    op.drop_index("uq_knowledge_bases_personal", table_name="knowledge_bases")
    op.drop_index("uq_knowledge_bases_project", table_name="knowledge_bases")
    op.drop_constraint("fk_kb_project_owner", "knowledge_bases", type_="foreignkey")
    op.drop_column("knowledge_bases", "project_id")
    op.create_unique_constraint("uq_knowledge_bases_user", "knowledge_bases", ["user_id"])
    op.drop_constraint("fk_threads_project_owner", "threads", type_="foreignkey")
    op.drop_column("threads", "project_id")
    op.drop_table("project_memories")
    op.drop_table("projects")
