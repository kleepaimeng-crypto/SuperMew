"""保留原文坐标的结构解析；不渲染 HTML、不访问链接、不调用模型。"""

import hashlib
import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Literal

from markdown_it import MarkdownIt


class DocumentBuildError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SourceSpan:
    # 原始 UTF-8 解码字符串的 Unicode code point，0-based，[start,end)。
    start: int
    end: int
    start_line: int
    end_line: int  # 行号 1-based，包含末行；CRLF 计一次换行。


@dataclass(frozen=True)
class ParsedSection:
    index: int
    parent_index: int | None
    level: int
    title_path: tuple[str, ...]
    span: SourceSpan  # 包含该节及下级章节；root 包含整个原文。


@dataclass(frozen=True)
class ParsedBlock:
    section_index: int
    kind: str
    span: SourceSpan
    atomic: bool = False


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    source_sha256: str
    source_format: Literal["markdown", "text"]
    line_starts: tuple[int, ...]
    sections: tuple[ParsedSection, ...]
    blocks: tuple[ParsedBlock, ...]

    def span(self, start: int, end: int) -> SourceSpan:
        return SourceSpan(
            start,
            end,
            bisect_right(self.line_starts, start),
            bisect_right(self.line_starts, max(start, end - 1)),
        )

    def excerpt(self, span: SourceSpan) -> str:
        return self.text[span.start : span.end]


def parse_source(
    content: bytes,
    source_format: str,
    expected_sha256: str,
    *,
    parser: Literal["markdown-txt-v1", "markdown-txt-v2"] = "markdown-txt-v2",
) -> ParsedDocument:
    if parser not in {"markdown-txt-v1", "markdown-txt-v2"}:
        raise DocumentBuildError("DOCUMENT_PARSER_UNSUPPORTED")
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise DocumentBuildError("DOCUMENT_SOURCE_HASH_MISMATCH")
    if source_format not in {"markdown", "text"}:
        raise DocumentBuildError("DOCUMENT_SOURCE_FORMAT_UNSUPPORTED")
    try:
        text = content.decode("utf-8")  # 不改 CRLF 或 BOM，定位始终基于原文。
    except UnicodeDecodeError as exc:
        raise DocumentBuildError("DOCUMENT_SOURCE_INVALID") from exc
    if not text.lstrip("\ufeff").strip() or "\0" in text:
        raise DocumentBuildError("DOCUMENT_SOURCE_INVALID")
    starts = (0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", text)))

    def span(start: int, end: int, trim: bool = True) -> SourceSpan:
        if trim:
            while start < end and (
                text[start].isspace() or (start == 0 and text[start] == "\ufeff")
            ):
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
        return SourceSpan(
            start, end, bisect_right(starts, start), bisect_right(starts, max(start, end - 1))
        )

    drafts = [{"parent": None, "level": 0, "titles": (), "start": 0, "end": len(text)}]
    blocks: list[ParsedBlock] = []
    stack = [0]

    def add_block(start: int, end: int, kind: str, atomic: bool = False) -> None:
        location = span(start, end, trim=not atomic)
        if location.start < location.end:
            blocks.append(ParsedBlock(stack[-1], kind, location, atomic))

    if source_format == "text":
        # 空白行分段；段内原始换行和空格保留。
        cursor = 0
        for match in re.finditer(r"(?>\r\n|\r|\n)[ \t]*(?>\r\n|\r|\n)", text):
            add_block(cursor, match.start(), "paragraph")
            cursor = match.end()
        add_block(cursor, len(text), "paragraph")
    else:
        tokens = MarkdownIt("commonmark").enable("table").parse(text.removeprefix("\ufeff"))
        roots = [
            i
            for i, token in enumerate(tokens)
            if token.level == 0 and token.map and token.type != "inline"
        ]
        cursor = 0
        for pos, index in enumerate(roots):
            token = tokens[index]
            first, last = token.map
            begin = starts[first]
            end = starts[last] if last < len(starts) else len(text)
            # 引用链接定义等不产生普通 block token 的原文也保留。
            add_block(cursor, begin, "text")
            if token.type == "heading_open":
                level = int(token.tag[1:])
                while len(stack) > 1 and drafts[stack[-1]]["level"] >= level:
                    drafts[stack.pop()]["end"] = begin
                title = tokens[index + 1].content.strip()
                parent = stack[-1]
                titles = drafts[parent]["titles"]
                # 空标题仍是结构节点；v2不把空元素写入可引用的标题路径。
                if title or parser == "markdown-txt-v1":
                    titles = (*titles, title)
                drafts.append(
                    {
                        "parent": parent,
                        "level": level,
                        "titles": titles,
                        "start": begin,
                        "end": len(text),
                    }
                )
                stack.append(len(drafts) - 1)
            else:
                next_index = roots[pos + 1] if pos + 1 < len(roots) else len(tokens)
                nested_atomic = any(
                    child.type in {"fence", "code_block", "table_open"}
                    for child in tokens[index:next_index]
                )
                kind = {
                    "paragraph_open": "paragraph",
                    "bullet_list_open": "list",
                    "ordered_list_open": "list",
                    "fence": "code",
                    "code_block": "code",
                    "table_open": "table",
                }.get(token.type, "text")
                add_block(begin, end, kind, nested_atomic)
            cursor = end
        add_block(cursor, len(text), "text")
    sections = tuple(
        ParsedSection(
            i,
            draft["parent"],
            draft["level"],
            draft["titles"],
            span(draft["start"], draft["end"], trim=False),
        )
        for i, draft in enumerate(drafts)
    )
    return ParsedDocument(text, expected_sha256, source_format, starts, sections, tuple(blocks))
