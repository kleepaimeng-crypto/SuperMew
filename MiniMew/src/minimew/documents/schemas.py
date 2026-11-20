from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from minimew.documents.profiles import BuildConfiguration

VersionStatus = Literal["candidate", "published", "failed", "superseded"]
JobStatus = Literal[
    "pending", "running", "staged", "succeeded", "failed", "cancelled", "dead_letter"
]
JobStage = Literal[
    "reserved", "parsing", "chunking", "embedding", "indexing", "verifying", "publishing", "ready"
]
JobError = Literal["INVALID_SOURCE", "SOURCE_UNAVAILABLE", "BUILD_FAILED"]


class PublicModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DocumentResponse(PublicModel):
    project_id: UUID | None = None
    deleted_at: datetime | None = None
    id: UUID
    knowledge_base_id: UUID
    title: str
    current_version_id: UUID | None
    pending_version_id: UUID | None
    created_at: datetime
    latest_job: "IndexJobResponse | None" = None


class DocumentPage(BaseModel):
    items: list[DocumentResponse]
    next_offset: int | None


class VersionResponse(PublicModel):
    id: UUID
    document_id: UUID
    version_number: int
    status: VersionStatus
    source_filename: str
    source_format: Literal["markdown", "text"]
    source_size: int
    content_sha256: str
    build_fingerprint: str
    build_configuration: BuildConfiguration
    created_at: datetime


class IndexJobResponse(PublicModel):
    id: UUID
    document_id: UUID
    version_id: UUID
    status: JobStatus
    stage: JobStage
    progress_done: int
    progress_total: int | None
    attempt: int
    error_code: JobError | None
    created_at: datetime
    finished_at: datetime | None


class UploadReceipt(BaseModel):
    document: DocumentResponse
    version: VersionResponse
    job: IndexJobResponse


class UploadKey(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class LocalOrBailianSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    source: Literal["local", "bailian"]
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def valid_key(self):
        if self.api_key is not None and (
            self.source != "bailian"
            or any(not 33 <= ord(c) <= 126 for c in self.api_key.get_secret_value())
        ):
            raise ValueError("Invalid API Key")
        return self


class EmbeddingChange(LocalOrBailianSelection):
    pass


class RerankChange(LocalOrBailianSelection):
    pass


class RerankSettingsView(BaseModel):
    source: Literal["local", "bailian"]
    model: str
    bailian_key_configured: bool


class EmbeddingRebuildView(BaseModel):
    id: UUID
    status: Literal["building", "succeeded", "failed"]
    target_model: str
    completed: int
    total: int
    error_code: str | None


class EmbeddingSettingsView(BaseModel):
    current_source: Literal["local", "bailian"]
    current_model: str
    dimensions: int
    bailian_key_configured: bool
    rebuild: EmbeddingRebuildView | None
