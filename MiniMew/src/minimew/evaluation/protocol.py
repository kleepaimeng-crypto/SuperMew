"""评测类型真源；区间使用Unicode字符的零基半开坐标。"""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=10000)]
Code = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")]
Outcome = Literal["answered", "no_knowledge", "insufficient", "waiting", "cancelled", "error"]
Mode = Literal["contract_smoke", "controlled", "live"]


class EvaluationInputError(ValueError):
    """公开诊断只使用稳定code，不附输入原文。"""

    def __init__(self, code: str, details: dict[str, int] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(code)


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False, validate_default=True
    )

    @model_validator(mode="after")
    def valid_strings(self):
        def check(value):
            if isinstance(value, str):
                if not value.strip() or "\0" in value:
                    raise ValueError("Nonblank UTF-8 text required")
                value.encode("utf-8")
            elif isinstance(value, tuple):
                for item in value:
                    check(item)

        for name in type(self).model_fields:
            check(getattr(self, name))
        return self


def canonical(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def fingerprint(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")


class Source(FrozenModel):
    source_id: Identifier
    content_sha256: Digest
    length: int = Field(gt=0)


class SourceText(FrozenModel):
    source_id: Identifier
    text: Text

    def identity(self) -> Source:
        return Source(
            source_id=self.source_id, content_sha256=text_hash(self.text), length=len(self.text)
        )


class Corpus(FrozenModel):
    schema_version: Literal[1] = 1
    sources: tuple[SourceText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identifiers(self):
        unique([s.source_id for s in self.sources], "source")
        return self


class Span(FrozenModel):
    source_id: Identifier
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Empty or reversed span")
        return self


class Case(FrozenModel):
    case_id: Identifier
    question: Text
    kind: Literal["quality", "fault"] = "quality"
    critical: bool = False
    allowed_outcomes: tuple[Outcome, ...] = Field(min_length=1)
    expected_error: Code | None = None
    evidence_gold: tuple[Span, ...] = ()
    required_claims: tuple[Text, ...] = ()
    forbidden_claims: tuple[Text, ...] = ()
    requires_citations: bool = False

    @model_validator(mode="after")
    def consistent(self):
        for name in ("allowed_outcomes", "required_claims", "forbidden_claims"):
            unique(getattr(self, name), name)
        unique([canonical(s) for s in self.evidence_gold], "gold span")
        if set(self.required_claims) & set(self.forbidden_claims):
            raise ValueError("Contradictory claims")
        if ("error" in self.allowed_outcomes) != (self.expected_error is not None):
            raise ValueError("Error outcome requires expected_error")
        if self.expected_error is not None and self.kind != "fault":
            raise ValueError("Quality cases cannot expect execution errors")
        if self.requires_citations and not self.evidence_gold:
            raise ValueError("Citation expectation requires source gold")
        return self


class Dataset(FrozenModel):
    schema_version: Literal[1] = 1
    dataset_id: Identifier
    sources: tuple[Source, ...] = Field(min_length=1)
    cases: tuple[Case, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self):
        unique([s.source_id for s in self.sources], "source")
        unique([c.case_id for c in self.cases], "case")
        lengths = {s.source_id: s.length for s in self.sources}
        for case in self.cases:
            for span in case.evidence_gold:
                if span.source_id not in lengths or span.end > lengths[span.source_id]:
                    raise ValueError("Gold outside declared source")
        return self

    def corpus_fingerprint(self) -> str:
        return fingerprint(
            [s.model_dump(mode="json") for s in sorted(self.sources, key=lambda s: s.source_id)]
        )


class Counts(FrozenModel):
    model_attempts: int = Field(ge=0)
    retrieval_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)


class Provenance(FrozenModel):
    mode: Mode
    implementation: Digest
    dependencies: Digest
    model: Digest
    strategy: Digest
    index: Digest


class Material(Span):
    material_id: Identifier
    content_sha256: Digest


class Observation(FrozenModel):
    case_id: Identifier
    outcome: Outcome
    error_code: Code | None = None
    materials: tuple[Material, ...] = ()
    citation_sources: tuple[Identifier, ...] = ()
    counts: Counts
    budget: Counts

    @model_validator(mode="after")
    def consistent(self):
        if (self.outcome == "error") != (self.error_code is not None):
            raise ValueError("Error status and code disagree")
        unique([m.material_id for m in self.materials], "material")
        unique(self.citation_sources, "citation source")
        return self


class ObservationSet(FrozenModel):
    schema_version: Literal[1] = 1
    dataset_fingerprint: Digest
    corpus_fingerprint: Digest
    provenance: Provenance
    cases: tuple[Observation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identifiers(self):
        unique([c.case_id for c in self.cases], "observation case")
        return self


T = TypeVar("T", bound=BaseModel)


def load(path: Path, model: type[T]) -> T:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvaluationInputError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    try:
        raw = path.read_text(encoding="utf-8")
        json.loads(raw, object_pairs_hook=pairs)
        return model.model_validate_json(raw)
    except EvaluationInputError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise EvaluationInputError("INVALID_EVALUATION_INPUT") from None


def validate_inputs(dataset: Dataset, corpus: Corpus, observations: ObservationSet):
    """完整可信输入边界；引用目录错误留作可评分的结构缺陷。"""
    actual = sorted((s.identity() for s in corpus.sources), key=lambda s: s.source_id)
    if actual != sorted(dataset.sources, key=lambda s: s.source_id):
        raise EvaluationInputError("CORPUS_MISMATCH")
    if observations.dataset_fingerprint != fingerprint(dataset):
        raise EvaluationInputError("DATASET_MISMATCH")
    if observations.corpus_fingerprint != dataset.corpus_fingerprint():
        raise EvaluationInputError("CORPUS_MISMATCH")
    expected = {c.case_id for c in dataset.cases}
    observed = {c.case_id for c in observations.cases}
    if observed - expected:
        raise EvaluationInputError("UNKNOWN_CASE")
    if expected - observed:
        raise EvaluationInputError(
            "MISSING_CASE",
            {
                "expected": len(expected),
                "observed": len(observed),
                "missing": len(expected - observed),
            },
        )
    texts = {s.source_id: s.text for s in corpus.sources}
    for observation in observations.cases:
        for material in observation.materials:
            text = texts.get(material.source_id)
            if (
                text is None
                or material.end > len(text)
                or text_hash(text[material.start : material.end]) != material.content_sha256
            ):
                raise EvaluationInputError("INVALID_SOURCE")
