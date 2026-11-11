"""显式准备与真实 CPU smoke；不读取数据库或回答模型凭据。"""

import argparse
import json
import sys
from time import perf_counter

from minimew.documents.profiles import preset
from minimew.embeddings.adapter import LocalEmbeddingAdapter
from minimew.embeddings.artifacts import local_snapshot, prepare_model
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError


def check_cpu(adapter: LocalEmbeddingAdapter) -> dict:
    texts = [
        "差旅报销 → 餐费\n员工出差期间，每日餐费报销上限为150元。",
        "账号管理 → 密码\n忘记登录密码时，可通过绑定邮箱重置密码。",
        "文档管理 → 上传\n知识库支持上传 Markdown 和 TXT 文件。",
    ]
    queries = ["出差每天吃饭最多能报销多少钱？", "忘记密码怎么办？"]
    started = perf_counter()
    documents = adapter.encode_documents(texts)
    observations = []
    for expected, query in enumerate(queries):
        vector = adapter.encode_query(query)
        scores = [sum(a * b for a, b in zip(vector, row)) for row in documents]
        ranking = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        observations.append(
            {"query": query, "ranking": ranking, "scores": scores, "expected_top": expected}
        )
    passed = all(row["ranking"][0] == row["expected_top"] for row in observations)
    return {
        "status": "ok" if passed else "failed",
        "device": "cpu",
        "documents": len(documents),
        "dimension": len(documents[0]),
        "encode_seconds": round(perf_counter() - started, 3),
        "observations": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "status", "check"])
    args = parser.parse_args()
    settings = EmbeddingRuntimeSettings()
    profile = preset("bge-small-zh-v1.5").embedding
    try:
        if args.command == "prepare":
            prepare_model(profile, settings)
            result = {"status": "prepared"}
        elif args.command == "status":
            local_snapshot(profile, settings)
            result = {"status": "files_present", "encoding_verified": False}
        else:
            started = perf_counter()
            adapter = LocalEmbeddingAdapter.load_cpu(profile, settings)
            load_seconds = perf_counter() - started
            result = check_cpu(adapter)
            result["load_seconds"] = round(load_seconds, 3)
        result.update(model_id=profile.model_id, revision=profile.revision)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["status"] == "failed" else 0
    except EmbeddingError as exc:
        print(json.dumps({"status": "error", "code": exc.code}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
