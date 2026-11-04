"""个人用户与对话的持久化模型；这里不执行权限校验。"""

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
            "status IN ('pending','running','succeeded','failed','cancelled')",
            name="ck_runs_status",
        ),
        CheckConstraint("last_event_sequence >= 0", name="ck_runs_event_sequence"),
        CheckConstraint(
            "(status = 'pending' AND owner_token IS NULL AND lease_until IS NULL AND "
            "started_at IS NULL AND finished_at IS NULL AND error_code IS NULL "
            "AND cancel_requested_at IS NULL) OR "
            "(status = 'running' AND owner_token IS NOT NULL AND lease_until IS NOT NULL AND "
            "started_at IS NOT NULL AND finished_at IS NULL AND error_code IS NULL) OR "
            "(status IN ('succeeded','failed') AND owner_token IS NOT NULL AND "
            "lease_until IS NOT NULL AND started_at IS NOT NULL AND finished_at IS NOT NULL AND "
            "((status = 'succeeded' AND error_code IS NULL AND cancel_requested_at IS NULL) OR "
            "(status = 'failed' AND error_code IS NOT NULL AND cancel_requested_at IS NULL))) OR "
            "(status='cancelled' AND cancel_requested_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND error_code IS NULL AND ((owner_token IS NULL AND lease_until IS NULL "
            "AND started_at IS NULL) OR (owner_token IS NOT NULL AND lease_until IS NOT NULL "
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
        CheckConstraint("sequence > 0", name="ck_messages_sequence"),
        CheckConstraint(
            "(role = 'user' AND status = 'completed') OR "
            "(role = 'assistant' AND status IN ('pending','completed','failed','cancelled'))",
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
            "'run.completed','run.failed','run.cancel_requested','run.cancelled')",
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
