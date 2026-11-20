"""Add knowledge-base embedding credentials and durable rebuilds."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("knowledge_bases", sa.Column("embedding_credential_id", UUID()))
    op.create_foreign_key(
        "fk_kb_embedding_credential_owner",
        "knowledge_bases",
        "model_credentials",
        ["embedding_credential_id", "user_id"],
        ["id", "user_id"],
    )
    op.create_table(
        "embedding_rebuilds",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("knowledge_base_id", UUID(), nullable=False),
        sa.Column("user_id", UUID(), nullable=False),
        sa.Column("credential_id", UUID()),
        sa.Column("configuration", JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'building'")),
        sa.Column("error_code", sa.String(32)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "id", "knowledge_base_id", "user_id", name="uq_embedding_rebuild_scope"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "user_id"],
            ["knowledge_bases.id", "knowledge_bases.user_id"],
            name="fk_embedding_rebuild_kb",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id", "user_id"],
            ["model_credentials.id", "model_credentials.user_id"],
            name="fk_embedding_rebuild_credential",
        ),
        sa.CheckConstraint(
            "status IN ('building','succeeded','failed')", name="ck_embedding_rebuild_status"
        ),
    )
    op.create_index(
        "uq_embedding_rebuild_active",
        "embedding_rebuilds",
        ["knowledge_base_id"],
        unique=True,
        postgresql_where=sa.text("status = 'building'"),
    )
    op.create_index(
        "ix_embedding_rebuild_history",
        "embedding_rebuilds",
        ["knowledge_base_id", "created_at", "id"],
    )
    op.add_column("index_jobs", sa.Column("rebuild_id", UUID()))
    op.add_column("index_jobs", sa.Column("source_version_id", UUID()))
    op.create_foreign_key(
        "fk_index_jobs_rebuild",
        "index_jobs",
        "embedding_rebuilds",
        ["rebuild_id", "knowledge_base_id", "user_id"],
        ["id", "knowledge_base_id", "user_id"],
    )
    op.create_foreign_key(
        "fk_index_jobs_rebuild_source",
        "index_jobs",
        "document_versions",
        ["source_version_id", "document_id", "user_id", "knowledge_base_id"],
        ["id", "document_id", "user_id", "knowledge_base_id"],
    )
    op.create_check_constraint(
        "ck_index_jobs_rebuild_source",
        "index_jobs",
        "(rebuild_id IS NULL) = (source_version_id IS NULL)",
    )
    op.create_index("ix_index_jobs_rebuild", "index_jobs", ["rebuild_id"])
    _job_stages(",'ready'")
    _guard_jobs(rebuilds=True)


def _job_stages(extra):
    op.drop_constraint("ck_index_jobs_stage", "index_jobs", type_="check")
    op.create_check_constraint(
        "ck_index_jobs_stage",
        "index_jobs",
        "stage IN ('reserved','parsing','chunking','embedding','indexing','verifying','publishing'"
        + extra
        + ")",
    )


def _guard_jobs(*, rebuilds):
    rebuild_identity = (
        """
      IF (OLD.rebuild_id, OLD.source_version_id)
         IS DISTINCT FROM (NEW.rebuild_id, NEW.source_version_id) THEN
         RAISE EXCEPTION 'Index rebuild binding is immutable' USING ERRCODE='23514';
      END IF;
      IF NEW.stage='ready' AND (NEW.rebuild_id IS NULL OR
          NEW.status NOT IN ('staged','succeeded','cancelled')) THEN
        RAISE EXCEPTION 'Only verified rebuild candidates can be ready' USING ERRCODE='23514';
      END IF;
      IF OLD.stage='ready' AND NEW IS DISTINCT FROM OLD AND
         (NEW.stage<>'ready' OR NEW.status NOT IN ('succeeded','cancelled') OR
          NEW.owner_token IS DISTINCT FROM OLD.owner_token) THEN
        RAISE EXCEPTION 'Ready candidate cannot return to worker' USING ERRCODE='23514';
      END IF;
    """
        if rebuilds
        else ""
    )
    ready_publication = (
        "AND NOT (OLD.stage='ready' AND OLD.rebuild_id IS NOT NULL AND NEW.status='succeeded')"
        if rebuilds
        else ""
    )
    op.execute(f"""
    CREATE OR REPLACE FUNCTION minimew_guard_index_job() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      {rebuild_identity}
      IF (OLD.id,OLD.version_id,OLD.document_id,OLD.user_id,OLD.knowledge_base_id,OLD.created_at)
        IS DISTINCT FROM
        (NEW.id,NEW.version_id,NEW.document_id,NEW.user_id,NEW.knowledge_base_id,NEW.created_at)
        THEN
        RAISE EXCEPTION 'Index Job identity is immutable' USING ERRCODE='23514';
      END IF;
      IF OLD.status IN ('succeeded','failed','cancelled','dead_letter')
         AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Terminal Index Job is immutable' USING ERRCODE='23514';
      END IF;
      IF OLD.owner_token IS DISTINCT FROM NEW.owner_token THEN
        IF NOT (OLD.status='pending' OR
           (OLD.status IN ('running','staged') AND OLD.lease_until<=clock_timestamp()
            AND (OLD.next_retry_at IS NULL OR OLD.next_retry_at<=clock_timestamp()))) OR
           NEW.status NOT IN ('running','staged') OR
           (OLD.status='staged' AND NEW.status<>'staged') OR
           NEW.execution_fence<>OLD.execution_fence+1 OR NEW.attempt<>OLD.attempt+1 OR
           NEW.lease_until<=clock_timestamp() THEN
          RAISE EXCEPTION 'Invalid Index Job claim' USING ERRCODE='23514';
        END IF;
      ELSE
        IF (OLD.execution_fence,OLD.attempt,OLD.worker_id) IS DISTINCT FROM
           (NEW.execution_fence,NEW.attempt,NEW.worker_id) THEN
          RAISE EXCEPTION 'Index Job fencing is immutable without claim' USING ERRCODE='23514';
        END IF;
        IF OLD.status IN ('running','staged') AND OLD.lease_until<=clock_timestamp()
           AND NEW.status<>'cancelled' {ready_publication} THEN
          RAISE EXCEPTION 'Index Job owner expired' USING ERRCODE='23514';
        END IF;
        IF OLD.status<>NEW.status AND NOT (
          (OLD.status='pending' AND NEW.status='cancelled') OR
          (OLD.status='running' AND NEW.status IN
            ('staged','succeeded','failed','cancelled','dead_letter')) OR
          (OLD.status='staged' AND NEW.status IN
            ('succeeded','failed','cancelled','dead_letter'))) THEN
          RAISE EXCEPTION 'Invalid Index Job transition' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END; $$
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM embedding_rebuilds) THEN
        RAISE EXCEPTION 'Embedding rebuild history prevents downgrade';
      END IF;
    END $$""")
    _guard_jobs(rebuilds=False)
    _job_stages("")
    op.drop_index("ix_index_jobs_rebuild", table_name="index_jobs")
    op.drop_constraint("ck_index_jobs_rebuild_source", "index_jobs", type_="check")
    op.drop_constraint("fk_index_jobs_rebuild_source", "index_jobs", type_="foreignkey")
    op.drop_constraint("fk_index_jobs_rebuild", "index_jobs", type_="foreignkey")
    op.drop_column("index_jobs", "source_version_id")
    op.drop_column("index_jobs", "rebuild_id")
    op.drop_table("embedding_rebuilds")
    op.drop_constraint("fk_kb_embedding_credential_owner", "knowledge_bases", type_="foreignkey")
    op.drop_column("knowledge_bases", "embedding_credential_id")
