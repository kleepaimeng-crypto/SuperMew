"""Independent evaluation corpora, jobs and case stages."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE evaluation_corpora (
        id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES users(id),
        collection VARCHAR(80) NOT NULL UNIQUE, payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE evaluation_jobs (
        id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES users(id),
        corpus_id UUID NOT NULL REFERENCES evaluation_corpora(id),
        idempotency_key VARCHAR(128) NOT NULL, request_hash VARCHAR(64) NOT NULL,
        snapshot JSONB NOT NULL, status VARCHAR(16) NOT NULL,
        cancel_requested BOOLEAN NOT NULL, owner_token UUID, lease_until TIMESTAMPTZ,
        deadline TIMESTAMPTZ NOT NULL, report JSONB, error_code VARCHAR(80),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_evaluation_jobs_key UNIQUE(user_id,idempotency_key),
        CONSTRAINT ck_evaluation_jobs_status CHECK(status IN
            ('pending','running','completed','failed','cancelled'))
    )""")
    op.execute("""CREATE TABLE evaluation_cases (
        id UUID PRIMARY KEY, job_id UUID NOT NULL REFERENCES evaluation_jobs(id),
        case_id VARCHAR(80) NOT NULL, status VARCHAR(16) NOT NULL,
        checkpoint JSONB, generation JSONB, judgment JSONB, progress JSONB, error_code VARCHAR(80),
        CONSTRAINT uq_evaluation_cases_job UNIQUE(job_id,case_id),
        CONSTRAINT ck_evaluation_cases_status CHECK(status IN
            ('pending','generating','waiting','accepted','generated','judging','completed','failed','cancelled'))
    )""")


def downgrade():
    op.drop_table("evaluation_cases")
    op.drop_table("evaluation_jobs")
    op.drop_table("evaluation_corpora")
