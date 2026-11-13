"""固定策略、阶段评分和不含正文的 Run 私有诊断。"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RagPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
    strategy: Literal["dense", "hybrid"] = "dense"
    recall_k: int = Field(default=10, ge=1, le=20)
    rrf_k: int = Field(default=60, ge=1, le=200)
    fused_k: int = Field(default=12, ge=1, le=20)
    candidate_limit: int = Field(default=18, ge=1, le=24)
    group_limit: int = Field(default=6, ge=1, le=6)
    final_k: int = Field(default=6, ge=1, le=6)
    merge: bool = True
    rerank: bool = True
    reranker: Literal["BAAI/bge-reranker-base"] = "BAAI/bge-reranker-base"
    revision: Literal["2cfc18c9415c912f9d8155881c133215df768a70"] = (
        "2cfc18c9415c912f9d8155881c133215df768a70"
    )
    window: Literal[512] = 512
    batch_size: Literal[1] = 1
    dtype: Literal["float32"] = "float32"


@dataclass(frozen=True)
class Candidate:
    row: dict
    dense: float | None = None
    bm25: float | None = None
    rrf: float | None = None
    rerank: float | None = None

    @property
    def key(self):
        p = self.row["payload"]
        return (str(p["identity"]["version_id"]), p["build_fingerprint"], self.row["chunk_id"])


@dataclass(frozen=True)
class MaterialGroup:
    members: tuple[Candidate, ...]
    completed: bool = False


@dataclass
class RetrievalTrace:
    strategy: str
    stages: dict = field(default_factory=dict)
    fallback: str | None = None
    attempts: int = 0
    limited: bool = False


@dataclass(frozen=True)
class RetrievalResult:
    groups: tuple[MaterialGroup, ...]
    trace: RetrievalTrace

    @property
    def rows(self):
        return tuple(c.row for g in self.groups for c in g.members)
