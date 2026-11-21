"""Archive conversations and freeze project retrieval model identities."""

import json
import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    # A staged candidate must be published/failed by the old version first.
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM embedding_rebuilds r
        JOIN knowledge_bases k ON k.id=r.knowledge_base_id
        WHERE k.project_id IS NOT NULL AND r.status='building') THEN
        RAISE EXCEPTION 'Finish project embedding rebuilds before migration 0026';
        END IF; END $$""")
    op.add_column(
        "threads",
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("model_credentials", sa.Column("retrieval_url", sa.String(2048)))
    # Deterministic migration of the old fixed endpoints; runtime never guesses missing metadata.
    op.execute("""UPDATE model_credentials SET
        retrieval_url='https://dashscope.aliyuncs.com/compatible-mode/v1'
        WHERE id IN (SELECT embedding_credential_id FROM knowledge_bases
                     UNION SELECT credential_id FROM embedding_rebuilds)""")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM model_credentials WHERE
        retrieval_url IS NOT NULL AND id IN (SELECT rerank_credential_id FROM knowledge_bases
            UNION SELECT rerank_credential_id FROM runs)) THEN
        RAISE EXCEPTION 'Retrieval credential has conflicting purposes'; END IF; END $$""")
    op.execute("""UPDATE model_credentials SET
        retrieval_url='https://dashscope.aliyuncs.com/compatible-api/v1/reranks'
        WHERE id IN (SELECT rerank_credential_id FROM knowledge_bases
                     UNION SELECT rerank_credential_id FROM runs)""")
    op.add_column("knowledge_bases", sa.Column("project_models", JSONB()))
    op.create_table(
        "retrieval_model_configurations",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("user_id", UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("configuration", JSONB(), nullable=False),
        sa.Column("credential_id", UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["credential_id", "user_id"],
            ["model_credentials.id", "model_credentials.user_id"],
            name="fk_retrieval_models_credential_owner",
        ),
    )
    # Projects that never uploaded inherited these deployment settings in 0025.
    from minimew.documents.profiles import BuildConfiguration, preset

    initial = BuildConfiguration.model_validate(
        {
            **preset(os.environ.get("MINIMEW_EMBEDDING_PROFILE", "bge-small-zh-v1.5")).model_dump(),
            "index_layout": os.environ.get("MINIMEW_DOCUMENT_INDEX_LAYOUT", "hybrid-jieba-bm25-v1"),
            "parser": "markdown-txt-v2",
            "chunker": "structure-v3",
        }
    )
    projects = sa.table("projects", sa.column("id"), sa.column("user_id"))
    bases = sa.table(
        "knowledge_bases",
        *[
            sa.column(name)
            for name in ("id", "user_id", "project_id", "build_configuration", "build_fingerprint")
        ],
    )
    op.execute(
        bases.insert().from_select(
            ["id", "user_id", "project_id", "build_configuration", "build_fingerprint"],
            sa.select(
                sa.func.gen_random_uuid(),
                projects.c.user_id,
                projects.c.id,
                sa.cast(op.inline_literal(json.dumps(initial.model_dump(mode="json"))), JSONB),
                op.inline_literal(initial.fingerprint()),
            ).where(~sa.exists(sa.select(bases.c.id).where(bases.c.project_id == projects.c.id))),
        )
    )
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM knowledge_bases
        WHERE project_id IS NOT NULL AND
        (build_configuration->'embedding'->>'model_id' IS NULL OR
         (build_configuration->'embedding'->>'id'='bailian-text-embedding-v4'
          AND embedding_credential_id IS NULL))) THEN
        RAISE EXCEPTION 'Project model configuration is incomplete'; END IF; END $$""")
    op.execute("""UPDATE knowledge_bases SET project_models=jsonb_build_object(
        'embedding', jsonb_build_object(
            'source', CASE WHEN build_configuration->'embedding'->>'id'='bailian-text-embedding-v4'
                      THEN 'bailian' ELSE 'local' END,
            'model_name', build_configuration->'embedding'->>'model_id',
            'base_url', CASE
                       WHEN build_configuration->'embedding'->>'id'='bailian-text-embedding-v4'
                       THEN 'https://dashscope.aliyuncs.com/compatible-mode/v1' ELSE NULL END),
        'rerank', jsonb_build_object(
            'source', CASE WHEN rerank_use_bailian THEN 'bailian' ELSE 'local' END,
            'model_name', CASE WHEN rerank_use_bailian THEN 'qwen3-rerank'
                          ELSE 'BAAI/bge-reranker-base' END,
            'base_url', CASE WHEN rerank_use_bailian
                       THEN 'https://dashscope.aliyuncs.com/compatible-api/v1/reranks'
                       ELSE NULL END))
        WHERE project_id IS NOT NULL""")
    op.create_check_constraint(
        "ck_kb_project_models", "knowledge_bases", "(project_id IS NULL) = (project_models IS NULL)"
    )
    op.execute("""CREATE FUNCTION minimew_guard_project_models() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF OLD.project_id IS NOT NULL AND (
            NEW.project_id IS DISTINCT FROM OLD.project_id
            OR NEW.user_id IS DISTINCT FROM OLD.user_id
            OR NEW.project_models IS DISTINCT FROM OLD.project_models
            OR NEW.build_configuration->'embedding'
                IS DISTINCT FROM OLD.build_configuration->'embedding'
            OR NEW.rerank_use_bailian IS DISTINCT FROM OLD.rerank_use_bailian) THEN
            RAISE EXCEPTION 'Project model identity is immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
        END $$""")
    op.execute(
        "CREATE TRIGGER guard_project_models BEFORE UPDATE ON knowledge_bases "
        "FOR EACH ROW EXECUTE FUNCTION minimew_guard_project_models()"
    )
    op.execute("""CREATE FUNCTION minimew_guard_retrieval_url() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.retrieval_url IS DISTINCT FROM OLD.retrieval_url THEN
            RAISE EXCEPTION 'Credential endpoint is immutable' USING ERRCODE='23514';
        END IF; RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER guard_retrieval_url BEFORE UPDATE ON model_credentials "
        "FOR EACH ROW EXECUTE FUNCTION minimew_guard_retrieval_url()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM threads WHERE archived)
        OR EXISTS(SELECT 1 FROM knowledge_bases WHERE project_models IS NOT NULL)
        OR EXISTS(SELECT 1 FROM retrieval_model_configurations)
        OR EXISTS(SELECT 1 FROM model_credentials WHERE retrieval_url IS NOT NULL) THEN
        RAISE EXCEPTION 'Cannot discard archive or project model bindings'; END IF; END $$""")
    op.execute("DROP TRIGGER guard_retrieval_url ON model_credentials")
    op.execute("DROP FUNCTION minimew_guard_retrieval_url()")
    op.execute("DROP TRIGGER guard_project_models ON knowledge_bases")
    op.execute("DROP FUNCTION minimew_guard_project_models()")
    op.drop_constraint("ck_kb_project_models", "knowledge_bases", type_="check")
    op.drop_table("retrieval_model_configurations")
    op.drop_column("knowledge_bases", "project_models")
    op.drop_column("model_credentials", "retrieval_url")
    op.drop_column("threads", "archived")
