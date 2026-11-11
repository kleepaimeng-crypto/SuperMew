"""结构优先分块；同节有限重叠，所有产物携带版本和构建身份。"""

import hashlib
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from minimew.documents.parsing import DocumentBuildError, ParsedDocument, SourceSpan, parse_source
from minimew.documents.profiles import BuildConfiguration, EmbeddingProfile
from minimew.documents.storage import SourceStore


class BudgetTokenizer(Protocol):
    profile: EmbeddingProfile

    def count(self, text: str) -> int: ...

    def offsets(self, text: str) -> list[tuple[int, int]]: ...


@dataclass(frozen=True)
class BuildIdentity:
    user_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    version_id: UUID


@dataclass(frozen=True)
class Section:
    section_id: UUID
    parent_section_id: UUID | None
    title_path: tuple[str, ...]
    span: SourceSpan


@dataclass(frozen=True)
class Chunk:
    chunk_id: UUID
    identity: BuildIdentity
    build_fingerprint: str
    source_sha256: str
    section_id: UUID
    ordinal: int
    span: SourceSpan
    source_text: str
    embedding_text: str
    content_sha256: str
    embedding_sha256: str
    token_count: int


@dataclass(frozen=True)
class ChunkBuild:
    identity: BuildIdentity
    build_fingerprint: str
    source_sha256: str
    sections: tuple[Section, ...]
    chunks: tuple[Chunk, ...]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_chunks(
    parsed: ParsedDocument,
    identity: BuildIdentity,
    build: BuildConfiguration,
    tokenizer: BudgetTokenizer,
) -> ChunkBuild:
    if build.source_format != parsed.source_format or tokenizer.profile != build.embedding:
        raise DocumentBuildError("DOCUMENT_BUILD_CONFIG_MISMATCH")
    fingerprint = build.fingerprint()
    scope = (
        f"minimew:chunks:v1:{identity.user_id}:{identity.knowledge_base_id}:"
        f"{identity.document_id}:{identity.version_id}:{parsed.source_sha256}:{fingerprint}"
    )
    section_ids = [
        uuid5(NAMESPACE_URL, f"{scope}:section:{section.index}") for section in parsed.sections
    ]
    sections = tuple(
        Section(
            section_ids[section.index],
            section_ids[section.parent_index] if section.parent_index is not None else None,
            section.title_path,
            section.span,
        )
        for section in parsed.sections
    )
    chunks: list[Chunk] = []

    def emit(section_index: int, start: int, end: int) -> None:
        section = sections[section_index]
        source = parsed.text[start:end]
        prefix = " → ".join(section.title_path)
        encoded = f"{prefix}\n{source}" if prefix else source
        count = tokenizer.count(build.embedding.document_prefix + encoded)
        if count > build.chunk_tokens:
            raise DocumentBuildError("DOCUMENT_CHUNK_TOO_LARGE")
        content_sha, encoded_sha = _sha(source), _sha(encoded)
        ordinal = len(chunks)
        chunks.append(
            Chunk(
                uuid5(
                    NAMESPACE_URL, f"{scope}:{ordinal}:{start}:{end}:{content_sha}:{encoded_sha}"
                ),
                identity,
                fingerprint,
                parsed.source_sha256,
                section.section_id,
                ordinal,
                parsed.span(start, end),
                source,
                encoded,
                content_sha,
                encoded_sha,
                count,
            )
        )

    def split_run(section_index: int, spans: list[SourceSpan]) -> None:
        if not spans:
            return
        run_start, run_end = spans[0].start, spans[-1].end
        section = sections[section_index]
        title = " → ".join(section.title_path)
        prefix = build.embedding.document_prefix + (title + "\n" if title else "")
        if tokenizer.count(prefix) > build.chunk_tokens:
            raise DocumentBuildError("DOCUMENT_TITLE_TOO_LONG")
        text = parsed.text[run_start:run_end]
        offsets = [(run_start + a, run_start + b) for a, b in tokenizer.offsets(text)]
        if any(a < run_start or b > run_end or a >= b for a, b in offsets) or any(
            offsets[i][0] < offsets[i - 1][0] or offsets[i][1] < offsets[i - 1][1]
            for i in range(1, len(offsets))
        ):
            raise DocumentBuildError("DOCUMENT_TOKEN_ALIGNMENT_INVALID")
        token_starts = [a for a, _ in offsets]
        token_ends = [b for _, b in offsets]
        paragraphs = [span.end for span in spans]
        sentences = [
            run_start + match.end()
            for match in re.finditer(r"[。！？!?；;][”’\"']*|\.(?=\s|$)|\r\n|\r|\n", text)
        ]

        def fits(start: int, end: int) -> bool:
            return tokenizer.count(prefix + parsed.text[start:end]) <= build.chunk_tokens

        start = frontier = run_start
        while frontier < run_end:
            first = bisect_right(token_ends, start)
            last = min(first + build.chunk_tokens, len(offsets))
            end = run_end if last == len(offsets) else token_ends[last - 1]
            # 使用实际编码输入复核，Token 边界只是候选，不能以字符数代替预算。
            while end > frontier and not fits(start, end):
                last -= 1
                end = token_ends[last - 1] if last > first else start
            if end <= frontier:
                if start < frontier:
                    start = frontier  # overlap 妨碍进展时减到 0，不重复输出旧片段。
                    continue
                raise DocumentBuildError("DOCUMENT_UNSPLITTABLE_TEXT")
            if end < run_end:
                for boundaries in (paragraphs, sentences):
                    pos = bisect_right(boundaries, end) - 1
                    if pos >= 0 and boundaries[pos] > frontier and fits(start, boundaries[pos]):
                        end = boundaries[pos]
                        break
            emit(section_index, start, end)
            frontier = end
            if end == run_end:
                break
            right = bisect_right(token_ends, end)
            left = max(bisect_left(token_starts, start), right - build.overlap_tokens)
            start = token_starts[left] if build.overlap_tokens and left < right else end
            # 子串重新分词后也必须在 overlap 上限内；不解码 token 来重建原文。
            while (
                start < end
                and len(tokenizer.offsets(parsed.text[start:end])) > build.overlap_tokens
            ):
                left += 1
                start = token_starts[left] if left < right else end

    run: list[SourceSpan] = []
    current = None
    for block in parsed.blocks:
        if block.section_index != current or block.atomic:
            if current is not None:
                split_run(current, run)
            run = []
            current = block.section_index
        if block.atomic:
            title = " → ".join(sections[current].title_path)
            prefix = build.embedding.document_prefix + (title + "\n" if title else "")
            if tokenizer.count(prefix) > build.chunk_tokens:
                raise DocumentBuildError("DOCUMENT_TITLE_TOO_LONG")
            if tokenizer.count(prefix + parsed.excerpt(block.span)) > build.chunk_tokens:
                raise DocumentBuildError("DOCUMENT_BLOCK_TOO_LARGE")
            emit(current, block.span.start, block.span.end)
        else:
            run.append(block.span)
    if current is not None:
        split_run(current, run)
    if not chunks:
        raise DocumentBuildError("DOCUMENT_NO_CONTENT")
    return ChunkBuild(identity, fingerprint, parsed.source_sha256, sections, tuple(chunks))


def build_version_chunks(
    store: SourceStore,
    identity: BuildIdentity,
    source_sha256: str,
    build: BuildConfiguration,
    tokenizer: BudgetTokenizer,
) -> ChunkBuild:
    """未来 Worker 传入已鉴权/领取的版本身份；不在此更改 Job/current。"""
    try:
        content = store.read(identity.version_id, source_sha256)
    except OSError as exc:
        raise DocumentBuildError("DOCUMENT_SOURCE_UNAVAILABLE") from exc
    parsed = parse_source(content, build.source_format, source_sha256)
    return build_chunks(parsed, identity, build, tokenizer)
