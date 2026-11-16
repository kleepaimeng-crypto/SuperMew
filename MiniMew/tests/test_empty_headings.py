import hashlib
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_document_chunking import IDENTITY, CharacterTokenizer, configuration

from minimew.documents.chunking import build_chunks, build_version_chunks
from minimew.documents.parsing import parse_source
from minimew.documents.profiles import read_build
from minimew.documents.storage import SourceStore, validate_source
from minimew.execution.tools import ToolFailure
from minimew.knowledge import KnowledgeService
from minimew.vector_store.manifest import make_candidate


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("prefix,expected", [("", ()), ("# 制度\n", ("制度",))])
def test_blank_headings_keep_structure_without_empty_source_titles(newline, prefix, expected):
    text = (prefix + "##\n餐费150元。\n### \n提供工作餐时扣减。\n## 住宿\n住宿300元。\n").replace(
        "\n", newline
    )
    data = text.encode()
    parsed = parse_source(data, "markdown", hashlib.sha256(data).hexdigest())
    assert parsed.text == text
    unnamed = [s for s in parsed.sections if s.level in (2, 3) and s.title_path == expected]
    assert len(unnamed) == 2
    assert unnamed[1].parent_index == unnamed[0].index
    assert parsed.sections[-1].parent_index == unnamed[0].parent_index
    assert parsed.sections[-1].title_path == (*expected, "住宿")
    build = configuration(size=96).model_copy(update={"parser": "markdown-txt-v2"})
    chunks = build_chunks(parsed, IDENTITY, build, CharacterTokenizer())
    candidate = make_candidate(chunks, build, [[1.0] + [0.0] * 511 for _ in chunks.chunks], uuid4())
    scope = SimpleNamespace(user_id=IDENTITY.user_id, targets=(candidate.manifest,))
    for record in candidate.records:
        row = record.entity(candidate.manifest.scope)
        match = KnowledgeService.project({**row, "score": 0.5}, scope)
        assert all(t.strip() for t in match.title_path)
        assert text[match.start : match.end] == match.text
    assert "扣减" in "".join(c.source_text for c in chunks.chunks)


def test_parser_revision_changes_build_and_keeps_legacy_output(tmp_path):
    source = validate_source("rules.md", "#\n餐费150元。".encode(), 4096)
    store = SourceStore(tmp_path)
    store.write(IDENTITY.version_id, source)
    legacy = configuration()
    fixed = legacy.model_copy(update={"parser": "markdown-txt-v2"})
    assert read_build(legacy.model_dump(mode="json"), legacy.fingerprint()) == legacy
    assert fixed.fingerprint() != legacy.fingerprint()
    old = build_version_chunks(store, IDENTITY, source.sha256, legacy, CharacterTokenizer())
    new = build_version_chunks(store, IDENTITY, source.sha256, fixed, CharacterTokenizer())
    assert old.sections[1].title_path == ("",)
    assert new.sections[1].title_path == ()
    assert old.chunks[0].chunk_id != new.chunks[0].chunk_id
    assert old.chunks[0].source_text == new.chunks[0].source_text
    assert store.read(IDENTITY.version_id, source.sha256) == source.content


def test_source_contract_still_rejects_empty_title_and_tampered_text():
    data = "#\n餐费150元。".encode()
    build = configuration()
    parsed = parse_source(data, "markdown", hashlib.sha256(data).hexdigest(), parser=build.parser)
    chunks = build_chunks(parsed, IDENTITY, build, CharacterTokenizer())
    candidate = make_candidate(chunks, build, [[1.0] + [0.0] * 511], uuid4())
    scope = SimpleNamespace(user_id=IDENTITY.user_id, targets=(candidate.manifest,))
    row = {**candidate.records[0].entity(candidate.manifest.scope), "score": 0.5}
    with pytest.raises(ToolFailure, match="KNOWLEDGE_RESULT_INVALID"):
        KnowledgeService.project(row, scope)
    broken = replace(chunks.chunks[0], source_text="错误的正文")
    assert broken.content_sha256 != hashlib.sha256(broken.source_text.encode()).hexdigest()
    row["payload"]["source_text"] = broken.source_text
    row["payload"]["section"]["title_path"] = []
    with pytest.raises(ToolFailure, match="KNOWLEDGE_RESULT_INVALID"):
        KnowledgeService.project(row, scope)
