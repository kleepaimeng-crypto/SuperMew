"""纯离线v2语料与Judge结果类型，不导入运行配置。"""

from typing import Literal

from pydantic import Field

from minimew.evaluation.protocol import Corpus, FrozenModel, SourceText


class JudgeResult(FrozenModel):
    rubric_version: Literal["controlled-rubric-v1"] = "controlled-rubric-v1"
    correctness: int = Field(ge=0, le=2)
    completeness: int = Field(ge=0, le=2)
    grounding: int = Field(ge=0, le=2)
    reason: str = Field(min_length=1, max_length=500)


class SourceTextV2(SourceText):
    text: str = Field(min_length=1)


class CorpusV2(Corpus):
    schema_version: Literal[2] = 2
    sources: tuple[SourceTextV2, ...] = Field(min_length=1)
