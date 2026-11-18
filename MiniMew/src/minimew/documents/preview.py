"""本地预览不登记 Document/Version，不写 Job，不发布索引。"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from minimew.documents.chunking import BudgetTokenizer, BuildIdentity, ChunkBuild, build_chunks
from minimew.documents.parsing import DocumentBuildError, parse_source
from minimew.documents.profiles import BuildConfiguration, preset
from minimew.documents.storage import InvalidSource, validate_source
from minimew.embeddings.adapter import LocalEmbeddingAdapter
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError
from minimew.embeddings.tokenizer import LocalTokenizer


def build_preview(path: Path, tokenizer: BudgetTokenizer) -> tuple[BuildConfiguration, ChunkBuild]:
    try:
        with path.open("rb") as stream:
            content = stream.read(5 * 1024 * 1024 + 1)
        source = validate_source(path.name, content, 5 * 1024 * 1024)
    except OSError as exc:
        raise DocumentBuildError("DOCUMENT_PREVIEW_SOURCE_UNAVAILABLE") from exc
    except InvalidSource as exc:
        raise DocumentBuildError("DOCUMENT_SOURCE_INVALID") from exc
    build = BuildConfiguration.model_validate(
        {
            **preset("bge-small-zh-v1.5").model_dump(mode="json"),
            "source_format": source.format,
            "parser": "markdown-txt-v2",
            "chunker": "structure-v2",
        }
    )
    # 仅供检查：身份固定标注 local-preview，不能作为数据库登记/鉴权的证据。
    document = uuid5(NAMESPACE_URL, "minimew:local-preview:document:" + path.name)
    identity = BuildIdentity(
        uuid5(NAMESPACE_URL, "minimew:local-preview:user"),
        uuid5(NAMESPACE_URL, "minimew:local-preview:knowledge-base"),
        document,
        uuid5(document, source.sha256),
    )
    parsed = parse_source(content, source.format, source.sha256, parser=build.parser)
    return build, build_chunks(parsed, identity, build, tokenizer)


def preview_file(path: Path, tokenizer: BudgetTokenizer) -> dict:
    build, result = build_preview(path, tokenizer)
    return {
        "mode": "local_preview",
        "registered_version": False,
        "input_file": path.name,
        "position_unit": "unicode_codepoint_0_based_half_open",
        "line_unit": "1_based_inclusive",
        "build_configuration": build.model_dump(mode="json"),
        **asdict(result),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--encode", action="store_true", help="复用正式 Adapter，真实 CPU 编码")
    args = parser.parse_args()
    settings = EmbeddingRuntimeSettings()
    profile = preset("bge-small-zh-v1.5").embedding
    try:
        tokenizer = LocalTokenizer.load(profile, settings)
        result = preview_file(args.file, tokenizer)
        if args.encode:
            adapter = LocalEmbeddingAdapter.load_cpu(profile, settings)
            vectors = adapter.encode_documents(
                [chunk["embedding_text"] for chunk in result["chunks"]]
            )
            result["encoding"] = {
                "device": "cpu",
                "vectors": len(vectors),
                "dimension": len(vectors[0]),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (DocumentBuildError, EmbeddingError) as exc:
        print(json.dumps({"status": "error", "code": exc.code}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
