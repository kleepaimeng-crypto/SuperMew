"""个人用户、对话与文档的持久化模型；这里不执行权限校验。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    username: Mapped[str] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(Text)


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_threads_id_user"),
        CheckConstraint("last_sequence >= 0", name="ck_threads_sequence"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", name="fk_threads_user_id_users", ondelete="RESTRICT"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200))
    last_sequence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    """仅保存高熵随机凭据的 SHA-256 摘要，保留撤销记录用于重放判断。"""

    __tablename__ = "refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", name="fk_refresh_tokens_user_id_users", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["threads.id", "threads.user_id"],
            name="fk_runs_thread_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("thread_id", "idempotency_key", name="uq_runs_thread_key"),
        UniqueConstraint("id", "thread_id", name="uq_runs_id_thread"),
        CheckConstraint(
            "status IN ('pending','running','waiting_input','resume_pending',"
            "'succeeded','failed','cancelled')",
            name="ck_runs_status",
        ),
        CheckConstraint("last_event_sequence >= 0", name="ck_runs_event_sequence"),
        CheckConstraint(
            "(status = 'pending' AND owner_token IS NULL AND lease_until IS NULL AND "
            "started_at IS NULL AND finished_at IS NULL AND error_code IS NULL "
            "AND cancel_requested_at IS NULL) OR "
            "(status = 'running' AND owner_token IS NOT NULL AND lease_until IS NOT NULL AND "
            "started_at IS NOT NULL AND finished_at IS NULL AND error_code IS NULL) OR "
            "(status IN ('waiting_input','resume_pending') AND owner_token IS NULL AND "
            "lease_until IS NULL AND started_at IS NOT NULL AND finished_at IS NULL AND "
            "error_code IS NULL AND cancel_requested_at IS NULL) OR "
            "(status IN ('succeeded','failed') AND "
            "((owner_token IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(owner_token IS NULL AND lease_until IS NULL)) AND "
            "started_at IS NOT NULL AND finished_at IS NOT NULL AND "
            "((status = 'succeeded' AND error_code IS NULL AND cancel_requested_at IS NULL) OR "
            "(status = 'failed' AND error_code IS NOT NULL AND cancel_requested_at IS NULL))) OR "
            "(status='cancelled' AND cancel_requested_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND error_code IS NULL AND ((owner_token IS NULL AND lease_until IS NULL "
            ") OR (owner_token IS NOT NULL AND lease_until IS NOT NULL "
            "AND started_at IS NOT NULL)))",
            name="ck_runs_execution_state",
        ),
        Index(
            "uq_runs_running_thread",
            "thread_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        CheckConstraint(
            "(model_snapshot_json IS NULL AND model_catalog_hash IS NULL) OR "
            "(model_snapshot_json IS NOT NULL AND model_catalog_hash IS NOT NULL AND "
            "jsonb_typeof(model_snapshot_json) = 'object' AND "
            "model_catalog_hash ~ '^[0-9a-f]{64}$')",
            name="ck_runs_model_snapshot",
        ),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    thread_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    model_snapshot_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    model_catalog_hash: Mapped[str | None] = mapped_column(String(64))
    owner_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(40))
    last_event_sequence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "thread_id"],
            ["runs.id", "runs.thread_id"],
            name="fk_messages_run_thread",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("thread_id", "sequence", name="uq_messages_thread_sequence"),
        UniqueConstraint("run_id", "role", name="uq_messages_run_role"),
        UniqueConstraint("id", "run_id", name="uq_messages_id_run"),
        CheckConstraint("sequence > 0", name="ck_messages_sequence"),
        CheckConstraint(
            "(role = 'user' AND status = 'completed') OR "
            "(role = 'assistant' AND status IN "
            "('pending','waiting_input','completed','failed','cancelled'))",
            name="ck_messages_role_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    thread_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnswerRecord(Base):
    """仅保存答案依据；唯一正文仍属于Message。"""

    __tablename__ = "answer_materials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "run_id"],
            ["messages.id", "messages.run_id"],
            name="fk_answer_message_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint("jsonb_typeof(materials) = 'object'", name="ck_answer_materials_object"),
        CheckConstraint("octet_length(materials::text) <= 262144", name="ck_answer_materials_size"),
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="RESTRICT"), primary_key=True
    )
    message_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    materials: Mapped[dict] = mapped_column(JSONB(none_as_null=True))


class ModelProfile(Base):
    """可修改的无 Secret 配置；历史执行使用 Run 内的副本。"""

    __tablename__ = "model_profiles"
    __table_args__ = (
        UniqueConstraint("name", name="uq_model_profiles_name"),
        CheckConstraint("version > 0", name="ck_model_profiles_version"),
        CheckConstraint("jsonb_typeof(configuration) = 'object'", name="ck_model_profiles_config"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    configuration: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelAssignment(Base):
    """本阶段只有 answer 角色；迁移建立空选择行作为并发锁锚点。"""

    __tablename__ = "model_assignments"
    __table_args__ = (CheckConstraint("role = 'answer'", name="ck_model_assignments_role"),)
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("model_profiles.id", name="fk_model_assignments_profile", ondelete="RESTRICT"),
    )


class RunEventRecord(Base):
    """持久事实日志；公开形状由 contracts/run_event_v1.json 定义。"""

    __tablename__ = "run_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "thread_id"],
            ["runs.id", "runs.thread_id"],
            name="fk_run_events_run_thread",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "sequence", name="uq_run_events_sequence"),
        CheckConstraint("sequence > 0", name="ck_run_events_sequence"),
        CheckConstraint("schema_version = 1", name="ck_run_events_version"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="ck_run_events_data"),
        CheckConstraint(
            "type IN ('run.created','run.started','message.delta','message.completed',"
            "'run.completed','run.failed','run.cancel_requested','run.cancelled',"
            "'tool.started','tool.completed','tool.failed','hitl.required','hitl.accepted','run.resumed')",
            name="ck_run_events_type",
        ),
        Index(
            "uq_run_events_terminal",
            "run_id",
            unique=True,
            postgresql_where=text("type IN ('run.completed','run.failed','run.cancelled')"),
        ),
        Index(
            "uq_run_events_cancel_requested",
            "run_id",
            unique=True,
            postgresql_where=text("type = 'run.cancel_requested'"),
        ),
        Index(
            "uq_run_events_message_completed",
            "run_id",
            unique=True,
            postgresql_where=text("type = 'message.completed'"),
        ),
    )
    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    run_id: Mapped[UUID] = mapped_column(Uuid)
    thread_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSONB)


class RunCheckpoint(Base):
    """一次澄清的检查点及恢复接收记录；不存明文凭据，不属于公开 Run 字段。"""

    __tablename__ = "run_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(document) = 'object' AND octet_length(document::text) <= 8388608",
            name="ck_checkpoint_document",
        ),
        CheckConstraint("char_length(prompt) BETWEEN 1 AND 1024", name="ck_checkpoint_prompt"),
        CheckConstraint(
            "token_hash IS NULL OR token_hash ~ '^[0-9a-f]{64}$'", name="ck_checkpoint_token"
        ),
        CheckConstraint(
            "(accepted_at IS NULL AND resume_key IS NULL "
            "AND input_hash IS NULL AND answer IS NULL) OR "
            "(accepted_at IS NOT NULL AND resume_key IS NOT NULL AND "
            "input_hash ~ '^[0-9a-f]{64}$' AND char_length(answer) BETWEEN 1 AND 1024 "
            "AND token_hash IS NULL)",
            name="ck_checkpoint_receipt",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="RESTRICT"), unique=True
    )
    document: Mapped[dict] = mapped_column(JSONB)
    prompt: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    resume_key: Mapped[str | None] = mapped_column(String(128))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    answer: Mapped[str | None] = mapped_column(Text)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventOutbox(Base):
    """发送待办只关联 Journal；没有第二份正文或执行状态。"""

    __tablename__ = "event_outbox"
    __table_args__ = (
        CheckConstraint("attempts BETWEEN 0 AND 1000000000", name="ck_event_outbox_attempts"),
        CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN ('REDIS_UNAVAILABLE','EVENT_INVALID')",
            name="ck_event_outbox_error",
        ),
        Index(
            "ix_event_outbox_due",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )
    event_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "run_events.event_id",
            name="fk_event_outbox_event",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_error_code: Mapped[str | None] = mapped_column(String(32))


class KnowledgeBase(Base):
    """首版每用户一个个人知识库；目标构建配置不等于已发布检索配置。"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_knowledge_bases_user"),
        UniqueConstraint("id", "user_id", name="uq_knowledge_bases_owner"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    build_configuration: Mapped[dict] = mapped_column(JSONB)
    build_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "user_id", "knowledge_base_id", name="uq_documents_scope"),
        ForeignKeyConstraint(
            ["knowledge_base_id", "user_id"],
            ["knowledge_bases.id", "knowledge_bases.user_id"],
            name="fk_documents_kb_owner",
        ),
        ForeignKeyConstraint(
            ["current_version_id", "id", "user_id", "knowledge_base_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.user_id",
                "document_versions.knowledge_base_id",
            ],
            name="fk_documents_current",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["pending_version_id", "id", "user_id", "knowledge_base_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.user_id",
                "document_versions.knowledge_base_id",
            ],
            name="fk_documents_pending",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "last_version_number >= 0 AND publication_fence >= 0", name="ck_documents_counters"
        ),
        CheckConstraint(
            "current_version_id IS NULL OR pending_version_id IS NULL OR "
            "current_version_id <> pending_version_id",
            name="ck_documents_pointers",
        ),
        Index("ix_documents_owner_created", "user_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(Uuid)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(255))
    current_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    pending_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    last_version_number: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    publication_fence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "id", "document_id", "user_id", "knowledge_base_id", name="uq_document_versions_scope"
        ),
        UniqueConstraint("document_id", "version_number", name="uq_document_versions_number"),
        ForeignKeyConstraint(
            ["document_id", "user_id", "knowledge_base_id"],
            ["documents.id", "documents.user_id", "documents.knowledge_base_id"],
            name="fk_document_versions_scope",
        ),
        CheckConstraint(
            "status IN ('candidate','published','failed','superseded')",
            name="ck_document_versions_status",
        ),
        CheckConstraint("source_format IN ('markdown','text')", name="ck_document_versions_format"),
        CheckConstraint(
            "source_size > 0 AND version_number > 0 AND publication_fence > 0",
            name="ck_document_versions_numbers",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$' AND build_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_document_versions_hashes",
        ),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    document_id: Mapped[UUID] = mapped_column(Uuid)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    version_number: Mapped[int] = mapped_column(BigInteger)
    publication_fence: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'candidate'"))
    source_filename: Mapped[str] = mapped_column(String(255))
    source_format: Mapped[str] = mapped_column(String(16))
    source_size: Mapped[int] = mapped_column(BigInteger)
    content_sha256: Mapped[str] = mapped_column(String(64))
    build_configuration: Mapped[dict] = mapped_column(JSONB)
    build_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IndexJob(Base):
    __tablename__ = "index_jobs"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_index_jobs_version"),
        UniqueConstraint(
            "id",
            "version_id",
            "document_id",
            "user_id",
            "knowledge_base_id",
            name="uq_index_jobs_scope",
        ),
        ForeignKeyConstraint(
            ["version_id", "document_id", "user_id", "knowledge_base_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.user_id",
                "document_versions.knowledge_base_id",
            ],
            name="fk_index_jobs_scope",
        ),
        CheckConstraint(
            "status IN ('pending','running','staged','succeeded','failed','cancelled',"
            "'dead_letter')",
            name="ck_index_jobs_status",
        ),
        CheckConstraint(
            "stage IN ('reserved','parsing','chunking','embedding','indexing',"
            "'verifying','publishing')",
            name="ck_index_jobs_stage",
        ),
        CheckConstraint(
            "progress_done >= 0 AND (progress_total IS NULL OR "
            "progress_total >= progress_done) AND execution_fence >= 0 AND attempt >= 0",
            name="ck_index_jobs_progress",
        ),
        CheckConstraint(
            "(status='pending' AND owner_token IS NULL AND worker_id IS NULL "
            "AND lease_until IS NULL AND execution_fence=0 AND attempt=0) OR "
            "(status<>'pending' AND ((owner_token IS NOT NULL AND worker_id IS NOT NULL "
            "AND lease_until IS NOT NULL AND execution_fence>0 AND attempt>0) OR "
            "(status='cancelled' AND owner_token IS NULL AND worker_id IS NULL "
            "AND lease_until IS NULL AND execution_fence=0 AND attempt=0)))",
            name="ck_index_jobs_owner",
        ),
        CheckConstraint(
            "(status IN ('pending','running','staged') AND finished_at IS NULL "
            "AND error_code IS NULL) OR (status IN ('succeeded','cancelled') "
            "AND finished_at IS NOT NULL AND error_code IS NULL) OR "
            "(status IN ('failed','dead_letter') AND finished_at IS NOT NULL "
            "AND error_code IS NOT NULL)",
            name="ck_index_jobs_terminal",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ('INVALID_SOURCE',"
            "'SOURCE_UNAVAILABLE','BUILD_FAILED')",
            name="ck_index_jobs_error",
        ),
        Index("ix_index_jobs_claim", "status", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    version_id: Mapped[UUID] = mapped_column(Uuid)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    stage: Mapped[str] = mapped_column(String(20), server_default=text("'reserved'"))
    progress_done: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    progress_total: Mapped[int | None] = mapped_column(BigInteger)
    worker_id: Mapped[str | None] = mapped_column(String(200))
    owner_token: Mapped[UUID | None] = mapped_column(Uuid)
    execution_fence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    attempt: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class IndexArtifact(Base):
    """写向量前冻结的清单与分块；每次新构建拥有独立 Milvus namespace。"""

    __tablename__ = "index_artifacts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared','staged','published','retired')",
            name="ck_index_artifacts_status",
        ),
        UniqueConstraint("job_id", "execution_fence", name="uq_index_artifacts_execution"),
        Index("ix_index_artifacts_job_status", "job_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("index_jobs.id", ondelete="RESTRICT"))
    execution_fence: Mapped[int] = mapped_column(BigInteger)
    target_collection: Mapped[str] = mapped_column(String(100))
    manifest: Mapped[dict] = mapped_column(JSONB)
    chunks: Mapped[dict] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'prepared'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IndexCleanupJob(Base):
    """不可复活的 retired attempt 的精确清理账本；源文件不删除。"""

    __tablename__ = "index_cleanup_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','complete','dead_letter')",
            name="ck_index_cleanup_jobs_status",
        ),
        CheckConstraint("attempt >= 0 AND execution_fence >= 0", name="ck_index_cleanup_counters"),
        Index("ix_index_cleanup_due", "status", "due_at"),
    )
    artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey("index_artifacts.id", ondelete="RESTRICT"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_token: Mapped[UUID | None] = mapped_column(Uuid)
    execution_fence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    attempt: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IndexWorkerHeartbeat(Base):
    __tablename__ = "index_worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    fingerprints: Mapped[list] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentSubmission(Base):
    """提交键映射到原 Job；多个键可幂等复用同一个活跃内容构建。"""

    __tablename__ = "document_submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["knowledge_base_id", "user_id"],
            ["knowledge_bases.id", "knowledge_bases.user_id"],
            name="fk_document_submissions_owner",
        ),
        ForeignKeyConstraint(
            ["job_id", "version_id", "document_id", "user_id", "knowledge_base_id"],
            [
                "index_jobs.id",
                "index_jobs.version_id",
                "index_jobs.document_id",
                "index_jobs.user_id",
                "index_jobs.knowledge_base_id",
            ],
            name="fk_document_submissions_job",
        ),
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    version_id: Mapped[UUID] = mapped_column(Uuid)
    job_id: Mapped[UUID] = mapped_column(Uuid)
    request_sha256: Mapped[str] = mapped_column(String(64))
