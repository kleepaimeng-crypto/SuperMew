"""显式本地 CPU 检查：来源覆盖、真实分词预算与现有 Adapter 协作。"""

import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from minimew.documents.chunking import BuildIdentity, build_chunks
from minimew.documents.parsing import DocumentBuildError, parse_source
from minimew.documents.profiles import BuildConfiguration, preset
from minimew.embeddings.adapter import LocalEmbeddingAdapter
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError
from minimew.embeddings.tokenizer import LocalTokenizer


def main() -> int:
    profile = preset("bge-small-zh-v1.5").embedding
    settings = EmbeddingRuntimeSettings()
    build = BuildConfiguration.model_validate(
        {
            **preset("bge-small-zh-v1.5").model_dump(mode="json"),
            "source_format": "markdown",
            "chunker": "structure-v2",
        }
    )
    texts = {
        "sample": (Path(__file__).resolve().parents[1] / "examples/rag/expense-policy.md")
        .read_bytes()
        .decode("utf-8"),
        "long_crlf": "\ufeff# 制度\r\n## 餐费\r\n"
        + "员工因公出差期间，每日餐费上限150元，报销须附发票。" * 100,
        "long_table": "# 数据类型\n|类型|说明|\n|---|---|\n"
        + "\n".join(f"|类型{i}|" + "说明" * 24 + "|" for i in range(10))
        + "\n",
    }
    identity = BuildIdentity(
        *(uuid5(NAMESPACE_URL, f"minimew:check-chunking:{i}") for i in range(4))
    )
    try:
        tokenizer = LocalTokenizer.load(profile, settings)
        adapter = LocalEmbeddingAdapter.load_cpu(profile, settings)
        observations = []
        for name, text in texts.items():
            content = text.encode("utf-8")
            parsed = parse_source(content, "markdown", hashlib.sha256(content).hexdigest())
            result = build_chunks(parsed, identity, build, tokenizer)
            assert result == build_chunks(parsed, identity, build, tokenizer)
            covered = {i for row in result.chunks for i in range(row.span.start, row.span.end)}
            required = {
                i
                for block in parsed.blocks
                for i in range(block.span.start, block.span.end)
                if not text[i].isspace()
            }
            assert required <= covered
            for row in result.chunks:
                assert text[row.span.start : row.span.end] == row.source_text
                assert row.token_count == tokenizer.count(row.embedding_text) <= build.chunk_tokens
            for a, b in zip(result.chunks, result.chunks[1:]):
                if a.section_id != b.section_id:
                    assert a.span.end <= b.span.start
                elif b.span.start < a.span.end:
                    assert (
                        len(tokenizer.offsets(text[b.span.start : a.span.end]))
                        <= build.overlap_tokens
                    )
            vectors = adapter.encode_documents([row.embedding_text for row in result.chunks])
            assert len(vectors) == len(result.chunks)
            observations.append(
                {
                    "case": name,
                    "chunks": len(result.chunks),
                    "max_tokens": max(c.token_count for c in result.chunks),
                    "vectors": len(vectors),
                    "dimension": len(vectors[0]),
                }
            )
        for code in ["```\n" + "很长的代码" * 400 + "\n```", "# " + "很长的标题" * 400 + "\n正文"]:
            data = code.encode()
            parsed = parse_source(data, "markdown", hashlib.sha256(data).hexdigest())
            try:
                build_chunks(parsed, identity, build, tokenizer)
            except DocumentBuildError as exc:
                assert exc.code in {"DOCUMENT_TITLE_TOO_LONG", "DOCUMENT_BLOCK_TOO_LARGE"}
            else:
                raise AssertionError("Oversized structure must fail explicitly")
        print(
            json.dumps(
                {"status": "ok", "device": "cpu", "observations": observations},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (DocumentBuildError, EmbeddingError) as exc:
        print(json.dumps({"status": "error", "code": exc.code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
