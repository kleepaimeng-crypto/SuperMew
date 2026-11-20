"""仅描述构建能力；导入不加载权重、创建客户端或访问网络。"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LOCAL_DENSE = "dense-cosine-v1"
LOCAL_HYBRID = "hybrid-jieba-bm25-v1"
BAILIAN_DENSE = "bailian-dense-cosine-v1"
BAILIAN_HYBRID = "bailian-hybrid-jieba-bm25-v1"
BAILIAN_ID = "bailian-text-embedding-v4"
CURRENT_CHUNKER = "structure-v3"
BAILIAN_LAYOUTS = (BAILIAN_DENSE, BAILIAN_HYBRID)
HYBRID_LAYOUTS = (LOCAL_HYBRID, BAILIAN_HYBRID)


class FrozenConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EmbeddingProfile(FrozenConfiguration):
    id: Literal["bge-small-zh-v1.5", "bge-m3", "bailian-text-embedding-v4"]
    model_id: Literal["BAAI/bge-small-zh-v1.5", "BAAI/bge-m3", "text-embedding-v4"]
    revision: str = Field(pattern=r"^(?:[a-f0-9]{40}|bailian-v4-contract-v1)$")
    dimension: int
    normalize: Literal[True] = True
    pooling: Literal["cls", "provider"] = "cls"
    query_prefix: str
    document_prefix: Literal[""] = ""
    max_input_tokens: int = Field(ge=32, le=8192)

    @model_validator(mode="after")
    def matching_model(self):
        if self.id == BAILIAN_ID:
            if (
                self.model_id,
                self.revision,
                self.dimension,
                self.pooling,
                self.query_prefix,
                self.max_input_tokens,
            ) != (
                "text-embedding-v4",
                "bailian-v4-contract-v1",
                512,
                "provider",
                "",
                512,
            ):
                raise ValueError("Invalid Bailian preset")
            return self
        if len(self.revision) != 40 or self.pooling != "cls":
            raise ValueError("Invalid local model revision or pooling")
        small = self.id == "bge-small-zh-v1.5"
        if (self.model_id, self.dimension) != (
            ("BAAI/bge-small-zh-v1.5", 512) if small else ("BAAI/bge-m3", 1024)
        ):
            raise ValueError("Embedding identity and dimension disagree")
        if self.max_input_tokens > (512 if small else 8192):
            raise ValueError("Input budget exceeds model limit")
        return self


class BuildConfiguration(FrozenConfiguration):
    schema_version: Literal[1] = 1
    source_format: Literal["markdown", "text"] | None = None
    parser: Literal["markdown-txt-v1", "markdown-txt-v2"] = "markdown-txt-v1"
    chunker: Literal["structure-v1", "structure-v2", "structure-v3"] = "structure-v1"
    chunk_tokens: int = Field(ge=32, le=8192)
    overlap_tokens: int = Field(ge=0)
    embedding: EmbeddingProfile
    index_layout: Literal[
        "dense-cosine-v1",
        "hybrid-jieba-bm25-v1",
        "bailian-dense-cosine-v1",
        "bailian-hybrid-jieba-bm25-v1",
    ] = LOCAL_DENSE

    @model_validator(mode="after")
    def budgets(self):
        if (self.embedding.id == BAILIAN_ID) != (self.index_layout in BAILIAN_LAYOUTS):
            raise ValueError("Embedding and index layout disagree")
        if not self.overlap_tokens < self.chunk_tokens <= self.embedding.max_input_tokens:
            raise ValueError("Inconsistent chunk budgets")
        return self

    def fingerprint(self) -> str:
        value = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return hashlib.sha256(value.encode()).hexdigest()

    @property
    def tokenizer_profile(self) -> EmbeddingProfile:
        # The fixed cloud preset keeps the existing local chunk boundaries and budget.
        if self.embedding.id == BAILIAN_ID:
            return preset("bge-small-zh-v1.5").embedding
        return self.embedding


def preset(profile_id: str) -> BuildConfiguration:
    # Revisions checked against the official repositories on 2026-11-09.
    if profile_id == "bge-small-zh-v1.5":
        embedding = EmbeddingProfile(
            id=profile_id,
            model_id="BAAI/bge-small-zh-v1.5",
            revision="7999e1d3359715c523056ef9478215996d62a620",
            dimension=512,
            query_prefix="为这个句子生成表示以用于检索相关文章：",
            max_input_tokens=512,
        )
        return BuildConfiguration(chunk_tokens=384, overlap_tokens=48, embedding=embedding)
    if profile_id == "bge-m3":
        embedding = EmbeddingProfile(
            id=profile_id,
            model_id="BAAI/bge-m3",
            revision="5617a9f61b028005a4858fdac845db406aefb181",
            dimension=1024,
            query_prefix="",
            max_input_tokens=1024,
        )
        return BuildConfiguration(chunk_tokens=768, overlap_tokens=64, embedding=embedding)
    if profile_id == BAILIAN_ID:
        embedding = EmbeddingProfile(
            id=BAILIAN_ID,
            model_id="text-embedding-v4",
            revision="bailian-v4-contract-v1",
            dimension=512,
            pooling="provider",
            query_prefix="",
            max_input_tokens=512,
        )
        return BuildConfiguration(
            chunk_tokens=384, overlap_tokens=48, embedding=embedding, index_layout=BAILIAN_DENSE
        )
    raise ValueError("Unknown embedding preset")


def read_build(value: dict, fingerprint: str) -> BuildConfiguration:
    build = BuildConfiguration.model_validate(value)
    if build.fingerprint() != fingerprint:
        raise ValueError("Build fingerprint mismatch")
    return build


def hybrid_preset(profile_id: str) -> BuildConfiguration:
    """显式的新构建身份；旧 preset 和已冻结的 fingerprint 不变。"""
    layout = BAILIAN_HYBRID if profile_id == BAILIAN_ID else LOCAL_HYBRID
    return preset(profile_id).model_copy(update={"index_layout": layout})
