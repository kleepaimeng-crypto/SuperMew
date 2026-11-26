"""固定字符计量验证结构/来源；真实 Tokenizer 另做显式本地检查。"""

import hashlib
import random
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from test_embeddings import write_snapshot

from minimew.documents.chunking import BuildIdentity, build_chunks, build_version_chunks
from minimew.documents.parsing import DocumentBuildError, parse_source
from minimew.documents.preview import preview_file
from minimew.documents.profiles import BuildConfiguration, preset
from minimew.documents.storage import SourceStore, validate_source
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError
from minimew.embeddings.tokenizer import LocalTokenizer

IDENTITY = BuildIdentity(*(UUID(int=i) for i in range(1, 5)))


class CharacterTokenizer:
    profile = preset("bge-small-zh-v1.5").embedding

    def count(self, text):
        return len(self.offsets(text)) + 2

    def offsets(self, text):
        return [(i, i + 1) for i, char in enumerate(text) if not char.isspace()]


def configuration(source_format="markdown", size=48, overlap=8, chunker="structure-v1"):
    return BuildConfiguration.model_validate(
        {
            **preset("bge-small-zh-v1.5").model_dump(mode="json"),
            "source_format": source_format,
            "chunk_tokens": size,
            "overlap_tokens": overlap,
            "chunker": chunker,
        }
    )


def parse(text, source_format="markdown"):
    content = text.encode()
    return parse_source(content, source_format, hashlib.sha256(content).hexdigest())


def chunk(text, source_format="markdown", size=48, overlap=8, chunker="structure-v1"):
    parsed = parse(text, source_format)
    return parsed, build_chunks(
        parsed,
        IDENTITY,
        configuration(source_format, size, overlap, chunker),
        CharacterTokenizer(),
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_bom_chinese_unicode_and_line_positions_refer_to_original(newline):
    text = "\ufeff# 制度" + newline + newline + "餐费150元，出差适用🙂。" + newline
    parsed, result = chunk(text)
    row = result.chunks[0]
    assert parsed.text == text
    assert row.span.start_line == row.span.end_line == 3
    assert text[row.span.start : row.span.end] == row.source_text == "餐费150元，出差适用🙂。"
    assert row.embedding_text == "制度\n" + row.source_text
    assert row.source_sha256 == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_txt_single_newline_is_not_a_blank_line(newline):
    parsed = parse("第一行" + newline + "第二行" + newline + newline + "另一段", "text")
    assert len(parsed.blocks) == 2
    assert parsed.excerpt(parsed.blocks[0].span) == "第一行" + newline + "第二行"
    assert parsed.sections[0].title_path == ()


def test_heading_paths_lists_fences_tables_and_repeated_titles():
    text = (
        "# 制度\n## 规则\n- 第一项\n  - 子项\n\n```python\n# 不是标题\nprint(1)\n```\n"
        "\n|项目|额度|\n|---|---|\n|餐费|150|\n\n## 规则\n另一个同名章节。"
    )
    parsed, result = chunk(text, size=96)
    assert [s.title_path for s in parsed.sections] == [
        (),
        ("制度",),
        ("制度", "规则"),
        ("制度", "规则"),
    ]
    assert {b.kind for b in parsed.blocks} == {"list", "code", "table", "paragraph"}
    assert result.sections[2].section_id != result.sections[3].section_id
    assert result.sections[2].parent_section_id == result.sections[1].section_id
    code = next(row for row in result.chunks if "print(1)" in row.source_text)
    assert "# 不是标题" in code.source_text
    assert code.source_text.startswith("```python")
    assert code.source_text.endswith("```\n")
    table = next(row for row in result.chunks if "|餐费|" in row.source_text)
    assert "|项目|额度|" in table.source_text


def test_setext_heading_and_unparsed_reference_definition_are_retained():
    parsed, result = chunk("制度\n====\n\n[资料][a]\n\n[a]: https://example.com\n")
    assert parsed.sections[1].title_path == ("制度",)
    assert any("[a]: https://example.com" in row.source_text for row in result.chunks)


@pytest.mark.parametrize(
    "content,format,sha,code",
    [
        (b"abc", "text", "0" * 64, "DOCUMENT_SOURCE_HASH_MISMATCH"),
        (b"abc", "pdf", None, "DOCUMENT_SOURCE_FORMAT_UNSUPPORTED"),
        (b"\xff", "text", None, "DOCUMENT_SOURCE_INVALID"),
        (b"a\0b", "text", None, "DOCUMENT_SOURCE_INVALID"),
        (b" \n", "text", None, "DOCUMENT_SOURCE_INVALID"),
    ],
)
def test_source_errors_are_explicit(content, format, sha, code):
    with pytest.raises(DocumentBuildError, match=code):
        parse_source(content, format, sha or hashlib.sha256(content).hexdigest())


def test_long_paragraphs_cover_every_body_character_and_stay_in_budget():
    text = "# 制度\n## 餐费\n" + "出差期间餐费上限150元。" * 35 + "\n\n申请需票据。" * 10
    text += "\n## 住宿\n" + "住宿需要发票并且按城市分类。" * 12
    parsed, result = chunk(text)
    assert len(result.chunks) > 10
    expected = {
        i
        for block in parsed.blocks
        for i in range(block.span.start, block.span.end)
        if not text[i].isspace()
    }
    covered = set()
    for row in result.chunks:
        covered.update(range(row.span.start, row.span.end))
        assert row.source_text == text[row.span.start : row.span.end]
        assert row.token_count == CharacterTokenizer().count(row.embedding_text) <= 48
        section = next(s for s in result.sections if s.section_id == row.section_id)
        assert section.span.start <= row.span.start < row.span.end <= section.span.end
    assert expected <= covered
    overlaps = []
    for a, b in zip(result.chunks, result.chunks[1:]):
        if a.section_id != b.section_id:
            assert a.span.end <= b.span.start
        elif b.span.start < a.span.end:
            overlap = text[b.span.start : a.span.end]
            assert len(CharacterTokenizer().offsets(overlap)) <= 8
            overlaps.append(overlap)
    assert overlaps


def test_sentence_boundary_is_preferred_to_mid_sentence_cut():
    parsed, result = chunk(
        "# 节\n" + "甲" * 15 + "。" + "乙" * 20 + "。" + "丙" * 25, size=32, overlap=0
    )
    assert result.chunks[0].source_text == "甲" * 15 + "。"
    assert "".join(row.source_text for row in result.chunks) == parsed.excerpt(
        parsed.blocks[0].span
    )


def test_same_input_is_stable_but_versions_configs_and_owners_change_ids():
    parsed, first = chunk("# 节\n" + "报销规则。" * 30)
    assert first == build_chunks(parsed, IDENTITY, configuration(), CharacterTokenizer())
    variants = [
        build_chunks(
            parsed, replace(IDENTITY, version_id=UUID(int=9)), configuration(), CharacterTokenizer()
        ),
        build_chunks(
            parsed, replace(IDENTITY, user_id=UUID(int=9)), configuration(), CharacterTokenizer()
        ),
        build_chunks(parsed, IDENTITY, configuration(size=64), CharacterTokenizer()),
    ]
    for other in variants:
        assert set(row.chunk_id for row in first.chunks).isdisjoint(
            row.chunk_id for row in other.chunks
        )
    assert all(row.identity == IDENTITY for row in first.chunks)
    assert (
        first.chunks[0].content_sha256
        == hashlib.sha256(first.chunks[0].source_text.encode()).hexdigest()
    )


@pytest.mark.parametrize("source_format", [None, "text"])
def test_build_format_must_match_source(source_format):
    with pytest.raises(DocumentBuildError, match="DOCUMENT_BUILD_CONFIG_MISMATCH"):
        build_chunks(
            parse("# 标题\n正文"), IDENTITY, configuration(source_format), CharacterTokenizer()
        )


def test_tokenizer_must_match_frozen_embedding_profile():
    tokenizer = CharacterTokenizer()
    tokenizer.profile = preset("bge-m3").embedding
    with pytest.raises(DocumentBuildError, match="DOCUMENT_BUILD_CONFIG_MISMATCH"):
        build_chunks(parse("正文"), IDENTITY, configuration(), tokenizer)


@pytest.mark.parametrize(
    "block", ["```\n" + "甲" * 100 + "\n```", "|名|值|\n|---|---|\n|餐费|" + "甲" * 100 + "|"]
)
def test_legacy_oversized_code_or_table_fails_instead_of_silent_split(block):
    with pytest.raises(DocumentBuildError, match="DOCUMENT_BLOCK_TOO_LARGE"):
        chunk("# 标题\n" + block)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_oversized_table_splits_by_complete_rows_with_header_context(newline):
    lines = ["|类型|说明|", "|---|---|"] + [f"|第{i}类|说明文字{i}说明文字|" for i in range(8)]
    lines[5] = lines[5].replace("说明文字3", "说明文字3\u2028")
    text = "# 数据类型" + newline + newline.join(lines) + newline
    parsed, result = chunk(text, size=64, chunker="structure-v2")
    _, current = chunk(text, size=64, chunker="structure-v3")
    assert [row.source_text for row in current.chunks] == [row.source_text for row in result.chunks]
    rows = result.chunks
    assert len(rows) > 1
    header = newline.join(lines[:2]) + newline
    table = next(block for block in parsed.blocks if block.kind == "table")
    assert "".join(row.source_text for row in rows) == parsed.excerpt(table.span)
    assert rows[0].source_text.startswith(header)
    assert rows[0].table_header is None
    for row in rows[1:]:
        assert row.table_header == header
        assert row.embedding_text == "数据类型\n" + header + row.source_text
        assert row.source_text.startswith("|第")
    assert all(row.token_count <= 64 for row in rows)
    assert all(row.source_text == text[row.span.start : row.span.end] for row in rows)
    assert all(a.span.end == b.span.start for a, b in zip(rows, rows[1:]))
    assert result == build_chunks(
        parsed, IDENTITY, configuration(size=64, chunker="structure-v2"), CharacterTokenizer()
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_oversized_code_uses_original_contiguous_spans(newline):
    text = newline.join(["# 示例", "```java", "int a = 1;", "", "x" * 100, "```", ""])
    with pytest.raises(DocumentBuildError, match="DOCUMENT_BLOCK_TOO_LARGE"):
        chunk(text, size=48, chunker="structure-v2")

    parsed, result = chunk(text, size=48, chunker="structure-v3")
    code = next(block for block in parsed.blocks if block.kind == "code")
    assert len(result.chunks) > 2
    assert result.chunks[0].source_text.endswith(newline * 2)
    assert "".join(row.source_text for row in result.chunks) == parsed.excerpt(code.span)
    assert all(row.token_count <= 48 for row in result.chunks)
    assert all(
        left.span.end == right.span.start for left, right in zip(result.chunks, result.chunks[1:])
    )


def test_oversized_single_table_row_still_fails_without_truncation():
    with pytest.raises(DocumentBuildError, match="DOCUMENT_BLOCK_TOO_LARGE"):
        chunk("# 标题\n|名|值|\n|---|---|\n|餐费|" + "甲" * 100 + "|", chunker="structure-v2")


def test_nested_code_is_not_silently_split_as_an_ordinary_list():
    text = "# 标题\n- 列表\n\n    ```\n    " + "甲" * 100 + "\n    ```"
    with pytest.raises(DocumentBuildError, match="DOCUMENT_BLOCK_TOO_LARGE"):
        chunk(text)


def test_title_over_budget_is_not_silently_dropped():
    with pytest.raises(DocumentBuildError, match="DOCUMENT_TITLE_TOO_LONG"):
        chunk("# " + "甲" * 100 + "\n正文")


def test_heading_only_document_is_explicitly_empty():
    with pytest.raises(DocumentBuildError, match="DOCUMENT_NO_CONTENT"):
        chunk("# 只有标题")


def test_small_chunks_overlap_cannot_prevent_forward_progress():
    text = "# 节\n" + ("甲。\n\n" * 100)
    parsed, result = chunk(text, size=32, overlap=30)
    assert result.chunks[-1].span.end == parsed.blocks[-1].span.end
    assert all(b.span.end > a.span.end for a, b in zip(result.chunks, result.chunks[1:]))


def test_fixed_random_inputs_have_no_missing_non_whitespace_or_oversized_chunks():
    rng = random.Random(3)
    for _ in range(20):
        body = "".join(rng.choice("甲乙丙丁。！？\n ") for _ in range(500))
        parsed, result = chunk(body, "text", size=32, overlap=7)
        covered = {i for row in result.chunks for i in range(row.span.start, row.span.end)}
        assert {i for i, c in enumerate(body) if not c.isspace()} <= covered
        assert all(row.token_count <= 32 for row in result.chunks)
        assert result == build_chunks(
            parsed, IDENTITY, configuration("text", 32, 7), CharacterTokenizer()
        )


def test_formal_source_store_entry_uses_same_builder(tmp_path):
    content = "# 制度\n正文内容。".encode()
    source = validate_source("制度.md", content, 1024)
    store = SourceStore(tmp_path)
    store.write(IDENTITY.version_id, source)
    result = build_version_chunks(
        store, IDENTITY, source.sha256, configuration(), CharacterTokenizer()
    )
    assert result == build_chunks(
        parse(content.decode()), IDENTITY, configuration(), CharacterTokenizer()
    )


def test_preview_is_read_only_and_cannot_claim_registered_version(tmp_path):
    path = tmp_path / "制度.md"
    content = "# 制度\n正文内容。".encode()
    path.write_bytes(content)
    result = preview_file(path, CharacterTokenizer())
    assert result["mode"] == "local_preview"
    assert result["registered_version"] is False
    assert result["position_unit"] == "unicode_codepoint_0_based_half_open"
    assert result == preview_file(path, CharacterTokenizer())
    assert path.read_bytes() == content
    assert list(tmp_path.iterdir()) == [path]


def test_tokenizer_loads_only_local_fixed_snapshot_and_uses_raw_offsets(tmp_path, monkeypatch):
    settings = EmbeddingRuntimeSettings(cache_dir=tmp_path)
    path = write_snapshot(settings)
    backend = Mock()
    backend.encode.side_effect = [
        SimpleNamespace(ids=[101, 1, 2, 102]),
        SimpleNamespace(offsets=[(0, 1), (2, 3)]),
    ]
    factory = Mock(return_value=backend)
    monkeypatch.setitem(
        sys.modules,
        "tokenizers",
        SimpleNamespace(Tokenizer=SimpleNamespace(from_file=factory)),
    )
    tokenizer = LocalTokenizer.load(preset("bge-small-zh-v1.5").embedding, settings)
    assert tokenizer.count("甲 乙") == 4
    assert tokenizer.offsets("甲 乙") == [(0, 1), (2, 3)]
    factory.assert_called_once_with(str(path / "tokenizer.json"))
    backend.no_truncation.assert_called_once_with()
    backend.no_padding.assert_called_once_with()
    assert backend.encode.call_args_list[0].kwargs == {"add_special_tokens": True}
    assert backend.encode.call_args_list[1].kwargs == {"add_special_tokens": False}


def test_slow_tokenizer_is_not_used_for_source_alignment():
    with pytest.raises(EmbeddingError, match="EMBEDDING_TOKENIZER_UNSUPPORTED"):
        LocalTokenizer(preset("bge-small-zh-v1.5").embedding, SimpleNamespace(is_fast=False))


@pytest.mark.parametrize("missing", [False, True])
def test_formal_source_integrity_or_missing_file_never_becomes_empty_result(tmp_path, missing):
    store = SourceStore(tmp_path)
    if not missing:
        store.write(IDENTITY.version_id, validate_source("a.md", b"changed", 100))
    with pytest.raises(DocumentBuildError, match="DOCUMENT_SOURCE_UNAVAILABLE"):
        build_version_chunks(store, IDENTITY, "0" * 64, configuration(), CharacterTokenizer())


def test_parent_conditions_are_locatable_but_not_invented_in_child_text():
    text = "# 制度\n仅适用于出差期间。\n\n## 餐费\n每日上限150元。"
    parsed, result = chunk(text)
    child = result.chunks[-1]
    section = next(s for s in result.sections if s.section_id == child.section_id)
    parent = next(s for s in result.sections if s.section_id == section.parent_section_id)
    assert "仅适用于出差期间" not in child.embedding_text
    assert "仅适用于出差期间" in parsed.excerpt(parent.span)
