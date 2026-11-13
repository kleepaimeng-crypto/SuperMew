"""固定Base的本地CPU FP32评分；无下载、远程代码、截断或隐式重试。"""

import math
import time

from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.rag.protocol import RagPolicy


class RerankError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class LocalReranker:
    def __init__(self, tokenizer, model):
        self.tokenizer, self.model = tokenizer, model

    @classmethod
    def load(cls, settings=None):
        policy = RagPolicy()
        root = (settings or EmbeddingRuntimeSettings()).resolved_cache_dir
        path = root / "models--BAAI--bge-reranker-base" / "snapshots" / policy.revision
        if not all(
            (path / f).is_file()
            for f in ["config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json"]
        ):
            raise RerankError("RERANK_NOT_PREPARED")
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(
                path, local_files_only=True, trust_remote_code=False
            )
            model = (
                AutoModelForSequenceClassification.from_pretrained(
                    path, local_files_only=True, trust_remote_code=False, use_safetensors=True
                )
                .to("cpu")
                .float()
                .eval()
            )
            return cls(tokenizer, model)
        except Exception as exc:
            raise RerankError("RERANK_LOAD_FAILED") from exc

    def score(self, query, passages, *, deadline=None):
        import torch

        try:
            batches = []
            for passage in passages:
                encoded = self.tokenizer(query, passage, truncation=False, return_tensors="pt")
                if encoded["input_ids"].shape[-1] > 512:
                    raise RerankError("RERANK_INPUT_TOO_LONG")
                batches.append(encoded)
            values = []
            with torch.inference_mode():
                for encoded in batches:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise RerankError("RERANK_DEADLINE_EXCEEDED")
                    values.append(float(self.model(**encoded).logits.view(-1)[0]))
            if len(values) != len(passages) or not all(math.isfinite(x) for x in values):
                raise RerankError("RERANK_INVALID_OUTPUT")
            return values
        except RerankError:
            raise
        except Exception as exc:
            raise RerankError("RERANK_ENCODING_FAILED") from exc


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "status", "check"])
    args = parser.parse_args()
    settings = EmbeddingRuntimeSettings()
    policy = RagPolicy()
    files = [
        "config.json",
        "model.safetensors",
        "sentencepiece.bpe.model",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ]
    path = (
        settings.resolved_cache_dir
        / "models--BAAI--bge-reranker-base"
        / "snapshots"
        / policy.revision
    )
    try:
        if args.command == "prepare":
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=policy.reranker,
                revision=policy.revision,
                cache_dir=settings.resolved_cache_dir,
                allow_patterns=files,
                token=False,
                endpoint="https://huggingface.co",
                max_workers=2,
            )
        if not all((path / f).is_file() and (path / f).stat().st_size > 0 for f in files):
            raise RerankError("RERANK_NOT_PREPARED")
        result = {"files_present": True, "revision": policy.revision, "encoding_verified": False}
        if args.command == "check":
            model = LocalReranker.load(settings)
            scores = model.score(
                "出差餐费每日上限是多少？", ["出差餐费每日上限150元。", "忘记密码可以重置。"]
            )
            if scores[0] <= scores[1]:
                raise RerankError("RERANK_CHECK_FAILED")
            result.update(encoding_verified=True, device="cpu", dtype=policy.dtype, scores=scores)
        print(json.dumps(result))
        return 0
    except RerankError as exc:
        print(json.dumps({"code": exc.code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
