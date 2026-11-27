"""Run私有证据目录和混合答案协议；不执行检索，不拥有数据库事务。"""

import hashlib
import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from minimew.execution.messages import MAX_OUTPUT_CHARS, valid_text

MAX_EVIDENCE = 18
MAX_EVIDENCE_CHARS = 24000
DOCUMENT_HEADING = "## 文档依据"
SUPPLEMENT_HEADING = "## 模型补充（未由本次资料验证）"
ANSWER_INSTRUCTIONS = (
    "你正在进行混合知识问答。文档知识作为主要来源"
    "最终回答遵循以下文本协议：有文档依据时，先独占一行输出'## 文档依据'，"
    "随后每个非空段落必须用工具提供的[E1]等编号引用该段实际依据（列表项不要用空行隔开）。"
    "只能使用本次工具结果的citation_id，不能自行编造编号、来源或链接。"
    "模型常识、推断、建议必须另起独占一行的'## 模型补充（未由本次资料验证）'，"
    "该分区不得包含证据引用，不得把补充描述为文档规定。两个分区各最多一次，"
    "先文档后补充，不使用代码围栏包裹整个答案。没有可用文档依据时只输出补充分区，"
    "说明本次资料不足；没有模型补充时省略补充分区。不得输出空分区或分区外文字。"
)
REFERENCE = re.compile(r"\[E[1-9][0-9]*\]")


class EvidenceModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
        validate_default=True,
    )


class EvidenceSource(EvidenceModel):
    knowledge_base_id: UUID
    document_id: UUID
    version_id: UUID
    chunk_id: UUID
    section_id: UUID
    title_path: tuple[str, ...]
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def validate_source(self):
        if (
            not valid_text(self.text, 10000, nonblank=True)
            or self.end - self.start != len(self.text)
            or self.end_line < self.start_line
            or hashlib.sha256(self.text.encode()).hexdigest() != self.content_sha256
            or any(not valid_text(title, 2000, nonblank=True) for title in self.title_path)
        ):
            raise ValueError("Invalid evidence source")
        return self


class Evidence(EvidenceSource):
    citation_id: str = Field(pattern=r"^E[1-9][0-9]*$")


class EvidenceLimit(ValueError):
    pass


class EvidenceDirectory:
    """宿主私有来源目录；引用分配不拥有任何聊天或评测事务。"""

    def __init__(self, identity):
        from minimew.execution.identity import CaseIdentity, ChatIdentity

        if not isinstance(identity, (ChatIdentity, CaseIdentity)):
            raise ValueError("Typed execution identity required")
        self.identity = identity
        self.queried = False
        self.items: tuple[Evidence, ...] = ()

    def owned_by(self, identity):
        return self.identity == identity

    def propose(self, source: EvidenceSource, pending: tuple[Evidence, ...]) -> Evidence:
        source = EvidenceSource.model_validate(source)
        for item in (*self.items, *pending):
            if EvidenceSource(**item.model_dump(exclude={"citation_id"})) == source:
                return item
        item = Evidence(**source.model_dump(), citation_id=f"E{len(self.items) + len(pending) + 1}")
        values = (*self.items, *pending, item)
        if (
            len(values) > MAX_EVIDENCE
            or sum(len(v.model_dump_json()) for v in values) > MAX_EVIDENCE_CHARS
        ):
            raise EvidenceLimit("Evidence budget exceeded")
        return item

    def commit(self, pending: tuple[Evidence, ...]):
        self.items = (*self.items, *pending)


class EvidenceLedger(EvidenceDirectory):
    def __init__(self, user_id: UUID, thread_id: UUID, run_id: UUID):
        from minimew.execution.identity import ChatIdentity

        super().__init__(ChatIdentity(user_id, thread_id, run_id))
        self.owner = (user_id, thread_id, run_id)

    def owns(self, user_id, thread_id, run_id):
        return self.owner == (user_id, thread_id, run_id)


class AnswerSection(EvidenceModel):
    kind: Literal["document", "supplement"]
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class Citation(EvidenceModel):
    citation_id: str = Field(pattern=r"^E[1-9][0-9]*$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class AnswerMaterials(EvidenceModel):
    sections: tuple[AnswerSection, ...] = Field(min_length=1, max_length=2)
    citations: tuple[Citation, ...] = Field(max_length=200)
    evidence: tuple[Evidence, ...] = Field(max_length=MAX_EVIDENCE)


@dataclass(frozen=True)
class AnswerResult:
    content: str
    materials: AnswerMaterials | None = None


_SUBHEADING_LINE = re.compile(r"^#{3,6}[ \t]+\S.*$")
_THEMATIC_BREAK = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_FENCE_START = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")


def _document_blocks(body: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    fence: tuple[str, int] | None = None
    for line in body.strip().splitlines():
        if fence is None:
            match = _FENCE_START.match(line)
            if match:
                marker = match.group(1)
                if marker[0] != "`" or "`" not in match.group(2):
                    fence = (marker[0], len(marker))
                    current.append(line)
                    continue
            if not line.strip():
                if current:
                    blocks.append("\n".join(current))
                    current = []
                continue
            current.append(line)
        else:
            current.append(line)
            stripped = line.strip()
            if (
                len(stripped) >= fence[1]
                and stripped.startswith(fence[0] * fence[1])
                and set(stripped) == {fence[0]}
            ):
                fence = None
    if current:
        blocks.append("\n".join(current))
    return blocks


def _is_subheading_block(block: str) -> bool:
    lines = [line for line in block.splitlines() if line.strip()]
    return bool(lines) and all(_SUBHEADING_LINE.match(line) for line in lines)


def _is_fenced_code_block(block: str) -> bool:
    lines = block.splitlines()
    if len(lines) < 2:
        return False
    match = _FENCE_START.match(lines[0])
    if not match:
        return False
    marker = match.group(1)
    if marker[0] == "`" and "`" in match.group(2):
        return False
    closing = lines[-1].strip()
    return (
        len(closing) >= len(marker)
        and closing.startswith(marker[0] * len(marker))
        and set(closing) == {marker[0]}
    )


def uncited_document_paragraphs(content: str) -> list[dict[str, object]]:
    """返回文档依据分区中缺少引用的实质段落；用于校验与格式修复定位。"""
    headings = list(
        re.finditer(
            rf"(?m)^({re.escape(DOCUMENT_HEADING)}|{re.escape(SUPPLEMENT_HEADING)})\r?$",
            content,
        )
    )
    for index, heading in enumerate(headings):
        if heading.group(1) != DOCUMENT_HEADING:
            continue
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        return _uncited_blocks(content[start:end])
    return []


def _uncited_blocks(body: str) -> list[dict[str, object]]:
    blocks = _document_blocks(body)
    if not blocks:
        return []
    cited = [bool(REFERENCE.search(block)) for block in blocks]
    subheading = [_is_subheading_block(block) for block in blocks]
    fenced = [_is_fenced_code_block(block) for block in blocks]
    rule = [bool(_THEMATIC_BREAK.match(block)) for block in blocks]
    has_cited_prose = any(
        cited[i] and not subheading[i] and not fenced[i] and not rule[i] for i in range(len(blocks))
    )
    uncited: list[dict[str, object]] = []
    for i, block in enumerate(blocks):
        if cited[i]:
            continue
        if subheading[i]:
            # 子标题必须后续接有带引用的实质段落，不能悬空。
            has_following_cited = any(
                cited[j] and not subheading[j] and not fenced[j] and not rule[j]
                for j in range(i + 1, len(blocks))
            )
            if has_following_cited:
                continue
        elif (fenced[i] or rule[i]) and has_cited_prose:
            continue
        uncited.append({"paragraph_index": i + 1, "preview": block.strip()[:120]})
    return uncited


def parse_answer(content: str, evidence: tuple[Evidence, ...]) -> AnswerResult:
    """位置为正文Unicode字符偏移[start,end)；仅验证协议/来源，不冒充语义评判。"""
    if not valid_text(content, MAX_OUTPUT_CHARS, nonblank=True):
        raise ValueError("Invalid answer")
    headings = list(
        re.finditer(
            rf"(?m)^({re.escape(DOCUMENT_HEADING)}|{re.escape(SUPPLEMENT_HEADING)})\r?$", content
        )
    )
    if not headings or content[: headings[0].start()].strip():
        raise ValueError("Missing answer sections")
    kinds = ["document" if h.group(1) == DOCUMENT_HEADING else "supplement" for h in headings]
    if kinds not in (["document"], ["supplement"], ["document", "supplement"]):
        raise ValueError("Invalid answer section order")
    known = {item.citation_id: item for item in evidence}
    sections, citations, used = [], [], []
    for index, heading in enumerate(headings):
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        body = content[start:end]
        if not body.strip():
            raise ValueError("Empty answer section")
        kind = kinds[index]
        sections.append(AnswerSection(kind=kind, start=start, end=end))
        references = list(REFERENCE.finditer(body))
        if "[E" in REFERENCE.sub("", body) or (kind == "supplement" and references):
            raise ValueError("Invalid citation syntax or supplement citation")
        if kind == "document":
            if _uncited_blocks(body):
                raise ValueError("Document paragraph lacks citation")
            for ref in references:
                key = ref.group()[1:-1]
                if key not in known:
                    raise ValueError("Unknown citation")
                citations.append(
                    Citation(citation_id=key, start=start + ref.start(), end=start + ref.end())
                )
                if key not in used:
                    used.append(key)
    return AnswerResult(
        content,
        AnswerMaterials(
            sections=tuple(sections),
            citations=tuple(citations),
            evidence=tuple(known[key] for key in used),
        ),
    )


def validate_materials(content: str, materials: AnswerMaterials):
    parsed = parse_answer(content, materials.evidence)
    if parsed.materials != materials:
        raise ValueError("Answer materials and canonical Message disagree")
