"""显式真实CPU/Milvus候选策略对照；与check_knowledge正式发布/Run验收配合。"""

import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from check_runtime import temporary_database
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.documents.chunking import BuildIdentity, build_chunks
from minimew.documents.parsing import parse_source
from minimew.documents.profiles import hybrid_preset
from minimew.documents.retrieval import RetrievalScope
from minimew.embeddings.tokenizer import LocalTokenizer
from minimew.knowledge_runtime import KnowledgeRuntime
from minimew.rag.pipeline import RagPipeline
from minimew.rag.protocol import RagPolicy
from minimew.settings import Settings
from minimew.vector_store.adapter import VerifiedCandidate
from minimew.vector_store.manifest import PROFILE, make_candidate

ROOT = Path(__file__).resolve().parents[1]


async def main():
    report = {
        "mode": "owned_candidate_strategy_comparison",
        "published": False,
        "paid_calls": 0,
        "passed": False,
    }
    destination = ROOT / ".artifacts/rag-check.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report), encoding="utf-8")
    before = os.environ.get("MINIMEW_DB_NAME")
    runtime = KnowledgeRuntime()
    scopes = []
    engine = None
    try:
        async with temporary_database() as db:
            try:
                os.environ["MINIMEW_DB_NAME"] = db
                engine = create_engine(Settings())
                sessions = async_sessionmaker(engine)
                cold = time.monotonic()
                await runtime.start()
                report["cold_load_seconds"] = time.monotonic() - cold
                tokenizer = await runtime.sync(
                    LocalTokenizer.load, PROFILE, runtime.embedding_settings
                )
                identity = BuildIdentity(uuid4(), uuid4(), uuid4(), uuid4())
                build = hybrid_preset(PROFILE.id).model_copy(update={"source_format": "markdown"})
                for n in range(20, 81, 5):
                    source = (
                        "# 差旅制度\n## 餐费\n"
                        + "员工出差每日餐费上限150元，须持票据按规定报销。\n" * n
                        + "条款 E1024，型号 AB-123。例外：已由公司提供餐食时不得重复报销。\n"
                    ).encode()
                    parsed = parse_source(source, "markdown", hashlib.sha256(source).hexdigest())
                    chunks = build_chunks(parsed, identity, build, tokenizer)
                    if len(chunks.chunks) == 3:
                        break
                assert len(chunks.chunks) == 3, len(chunks.chunks)
                vectors = await runtime.sync(
                    runtime.encoder.encode_documents, [c.embedding_text for c in chunks.chunks]
                )
                candidate = make_candidate(chunks, build, vectors, uuid4())
                scopes.append(candidate.manifest.scope)
                await runtime.sync(runtime.index.stage, candidate)
                for identifier in ("E1024", "AB-123"):
                    lexical = await runtime.sync(
                        runtime.index.lexical, VerifiedCandidate(candidate.manifest), identifier
                    )
                    assert lexical and all(
                        identifier in r["payload"]["source_text"] for r in lexical
                    )
                assert not await runtime.sync(
                    runtime.index.lexical, VerifiedCandidate(candidate.manifest), "E1025"
                )
                report["lexical_identifier_checks"] = ["E1024", "AB-123", "E1025 absent"]
                scope = RetrievalScope(
                    identity.user_id,
                    (candidate.manifest,),
                    datetime.now(UTC) + timedelta(minutes=2),
                    (chunks,),
                )
                pipeline = RagPipeline(sessions, runtime)
                observations = []
                for name, options in [
                    ("dense", {"strategy": "dense"}),
                    ("hybrid", {"strategy": "hybrid", "merge": False, "rerank": False}),
                    ("completion", {"strategy": "hybrid", "merge": True, "rerank": False}),
                    ("reranked", {"strategy": "hybrid", "merge": True, "rerank": True}),
                ]:
                    policy = RagPolicy(fused_k=2, **options)
                    durations = []
                    for _ in range(3):
                        start = time.monotonic()
                        result = await pipeline.search(
                            "出差餐费上限与公司供餐例外是什么？",
                            scope,
                            identity.user_id,
                            policy,
                            deadline=time.monotonic() + 60,
                        )
                        durations.append(time.monotonic() - start)
                    rows = result.rows
                    assert rows and all(
                        r["payload"]["identity"]["user_id"] == str(identity.user_id) for r in rows
                    )
                    if name in {"completion", "reranked"}:
                        assert any(g.completed for g in result.groups) and len(rows) == 3
                        assert any("不得重复报销" in r["payload"]["source_text"] for r in rows)
                    if name == "reranked":
                        assert result.trace.fallback is None and all(
                            r["score_kind"] == "rerank_raw" for r in rows
                        )
                    ordered = sorted(durations)
                    observations.append(
                        {
                            "route": name,
                            "samples": 3,
                            "seconds": durations,
                            "sample_median_seconds": ordered[1],
                            "sample_max_seconds": ordered[-1],
                            "rows": len(rows),
                            "trace": asdict(result.trace),
                        }
                    )
                report.update(
                    verified=True,
                    observations=observations,
                    memory_note="3 smoke samples; not a reliable p95 or quality evaluation",
                )
                import ctypes
                from ctypes import wintypes

                class Counters(ctypes.Structure):
                    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                        (name, ctypes.c_size_t)
                        for name in [
                            "PeakWorkingSetSize",
                            "WorkingSetSize",
                            "QuotaPeakPagedPoolUsage",
                            "QuotaPagedPoolUsage",
                            "QuotaPeakNonPagedPoolUsage",
                            "QuotaNonPagedPoolUsage",
                            "PagefileUsage",
                            "PeakPagefileUsage",
                        ]
                    ]

                if os.name == "nt":
                    counter = Counters()
                    counter.cb = ctypes.sizeof(counter)
                    handle = ctypes.windll.kernel32.GetCurrentProcess
                    handle.restype = wintypes.HANDLE
                    memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
                    memory_info.argtypes = [
                        wintypes.HANDLE,
                        ctypes.POINTER(Counters),
                        wintypes.DWORD,
                    ]
                    memory_info.restype = wintypes.BOOL
                    if memory_info(handle(), ctypes.byref(counter), counter.cb):
                        report["working_set_bytes"] = counter.WorkingSetSize
                # Dispose before temporary_database drops its database.
                await engine.dispose()
                engine = None
            finally:
                if engine:
                    await engine.dispose()
                    engine = None
        report["passed"] = True

    finally:
        for scope in scopes:
            await runtime.sync(runtime.index.delete_candidate, scope)
        await runtime.aclose()
        if engine:
            await engine.dispose()
        if before is None:
            os.environ.pop("MINIMEW_DB_NAME", None)
        else:
            os.environ["MINIMEW_DB_NAME"] = before
        report["cleanup_scopes"] = len(scopes)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
