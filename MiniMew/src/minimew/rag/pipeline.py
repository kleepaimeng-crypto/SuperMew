"""同一已发布 scope 内的唯一策略编排；授权和证据登记留在知识工具。"""

import asyncio
import math
import time
from dataclasses import replace

from minimew.documents.retrieval import scoped_index, search_published
from minimew.embeddings.errors import EmbeddingError
from minimew.execution.process import report_step
from minimew.rag.fusion import fuse
from minimew.rag.merging import complete, select
from minimew.rag.protocol import (
    Candidate,
    MaterialGroup,
    RagPolicy,
    RetrievalResult,
    RetrievalTrace,
)
from minimew.rag.reranker import RerankError
from minimew.vector_store.manifest import BAILIAN_PROFILE, PROFILE, VectorError, profile_for_layout


class RagPipeline:
    def __init__(self, sessions, runtime, *, retriever=search_published):
        self.sessions, self.runtime, self.retriever = sessions, runtime, retriever

    async def search(
        self,
        query,
        scope,
        user_id,
        policy=None,
        *,
        deadline=None,
        process=None,
        step_key="retrieval",
    ):
        async with report_step(
            process,
            "retrieval",
            step_key,
            query=query,
            phase={
                "initial": "initial",
                "rewrite-search": "rewritten",
                "clarified-search": "clarified",
                "search": "child",
            }.get(step_key, ""),
        ) as step:
            result = await self._search(
                query,
                scope,
                user_id,
                policy,
                deadline=deadline,
                process=step.reporter if step else None,
            )
            if step:
                step.finish(
                    summary="已保留检索材料",
                    count=len(result.rows),
                    sources=process_sources(result.rows, scope),
                    source_details=process_source_details(result.rows, scope),
                    outcome=result.trace.fallback or "ok",
                )
            return result

    async def _search(self, query, scope, user_id, policy=None, *, deadline=None, process=None):
        from types import SimpleNamespace

        runtime = self.runtime
        if getattr(scope, "namespace", None) is not None:
            runtime = SimpleNamespace(
                index=scoped_index(runtime.index, scope.namespace),
                sync=runtime.sync,
                encoder=runtime.encoder,
                reranker=runtime.reranker,
            )
        policy = policy or RagPolicy()
        trace = RetrievalTrace(policy.strategy)
        started = time.monotonic()
        profiles = {profile_for_layout(manifest.scope.index_layout) for manifest in scope.targets}
        if len(profiles) > 1:
            raise VectorError("VECTOR_BUILD_MISMATCH")
        profile = next(iter(profiles), PROFILE)
        if profile == BAILIAN_PROFILE:
            if self.runtime.bailian is None:
                raise EmbeddingError("EMBEDDING_CREDENTIAL_UNAVAILABLE")
            try:
                async with asyncio.timeout_at(deadline):
                    (vector,) = await self.runtime.bailian.encode(
                        user_id, scope.embedding_credential_id, [query]
                    )
            except TimeoutError:
                raise EmbeddingError("EMBEDDING_PROVIDER_TIMEOUT") from None
        else:
            vector = await runtime.sync(runtime.encoder.encode_query, query)
        hybrid = policy.strategy == "hybrid" and all(
            "bm25" in runtime.index.capabilities(m) for m in scope.targets
        )
        if policy.strategy == "hybrid" and not hybrid:
            trace.fallback = "HYBRID_LAYOUT_UNAVAILABLE"
        async with report_step(process, "dense", "dense") as stage:
            hits = await self.retriever(
                self.sessions,
                runtime.index,
                scope,
                user_id,
                vector,
                profile,
                top_k=policy.recall_k if hybrid else 3,
                offload=runtime.sync,
            )
            if stage:
                stage.finish(
                    summary="向量召回完成", count=len(hits), sources=process_sources(hits, scope)
                )
        trace.stages = {"dense_count": len(hits), "seconds": time.monotonic() - started}
        if hybrid:
            async with report_step(process, "bm25", "bm25") as stage:
                lexical = await self.retriever(
                    self.sessions,
                    runtime.index,
                    scope,
                    user_id,
                    vector,
                    profile,
                    top_k=policy.recall_k,
                    offload=runtime.sync,
                    lexical_query=query,
                )
                if stage:
                    stage.finish(
                        summary="关键词召回完成",
                        count=len(lexical),
                        sources=process_sources(lexical, scope),
                    )
            async with report_step(process, "fusion", "fusion") as stage:
                candidates = fuse(hits, lexical, policy)
                if stage:
                    stage.finish(summary="召回融合完成", count=len(candidates))
            trace.stages.update(bm25_count=len(lexical), fused_count=len(candidates))
            async with report_step(process, "merge", "merge") as stage:
                groups = await complete(candidates, scope, runtime, policy, trace)
                if stage:
                    stage.finish(
                        summary="同节材料补全完成", count=sum(len(g.members) for g in groups)
                    )
            if policy.rerank and candidates:
                async with report_step(process, "rerank", "rerank") as stage:
                    trace.attempts = 1
                    pairs = tuple(c for g in groups for c in g.members)
                    rerank_started = time.monotonic()
                    try:
                        values = await runtime.sync(
                            runtime.reranker.score,
                            query,
                            [c.row["payload"]["embedding_text"] for c in pairs],
                            deadline=deadline,
                        )
                        if len(values) != len(pairs) or not all(math.isfinite(v) for v in values):
                            raise RerankError("RERANK_INVALID_OUTPUT")
                        scores = {c.key: s for c, s in zip(pairs, values)}
                        groups = tuple(
                            replace(
                                g,
                                members=tuple(replace(c, rerank=scores[c.key]) for c in g.members),
                                seeds=tuple(replace(c, rerank=scores[c.key]) for c in g.seeds),
                            )
                            for g in groups
                        )
                        groups = tuple(
                            sorted(
                                groups,
                                key=lambda g: (
                                    -max(c.rerank for c in g.members),
                                    -max(c.rrf or 0 for c in g.members),
                                    g.members[0].key,
                                ),
                            )
                        )
                    except RerankError as exc:
                        if exc.code == "RERANK_DEADLINE_EXCEEDED":
                            from minimew.execution.tools import ToolFailure

                            raise ToolFailure("AGENT_DEADLINE_EXCEEDED") from None
                        trace.fallback = exc.code
                    trace.stages["rerank_seconds"] = time.monotonic() - rerank_started
                    if stage:
                        stage.finish(
                            summary="重排结束", count=len(pairs), outcome=trace.fallback or "ok"
                        )
            trace.stages["seconds"] = time.monotonic() - started
            return RetrievalResult(select(groups, policy.final_k, trace), trace)
        return RetrievalResult(
            tuple(MaterialGroup((Candidate(row, dense=row["score"]),)) for row in hits), trace
        )


def process_sources(rows, scope):
    """只公开当前返回候选的有界来源；不序列化向量、原文或整个scope。"""
    sources = {}
    for row in rows:
        payload = row["payload"]
        identity = payload["identity"]
        document_id, version_id = identity["document_id"], identity["version_id"]
        key = str(document_id), str(version_id)
        if key in sources or len(sources) >= 6:
            continue
        title = "文档 " + str(document_id)[:8]
        for build in getattr(scope, "directory", ()):
            section = next(
                (x for x in build.sections if str(x.section_id) == str(payload["section_id"])), None
            )
            if section and section.title_path:
                title = " / ".join(section.title_path)
                break
        from uuid import UUID

        sources[key] = {
            "document_id": UUID(str(document_id)),
            "version_id": UUID(str(version_id)),
            "title": title[:200],
        }
    return tuple(sources.values())


def process_source_details(rows, scope):
    from uuid import UUID

    catalog = {(str(d.document_id), str(d.version_id)): d for d in getattr(scope, "documents", ())}
    sources = {}
    for row in rows:
        payload = row["payload"]
        identity = payload["identity"]
        key = str(identity["document_id"]), str(identity["version_id"])
        if key not in sources:
            if len(sources) >= 6:
                continue
            document = catalog.get(key)
            sources[key] = {
                "document_id": UUID(key[0]),
                "version_id": UUID(key[1]),
                "name": document.name[:255] if document else "文档名称暂不可用",
                "version_number": document.version_number if document else 0,
                "sections": [],
            }
        for build in getattr(scope, "directory", ()):
            section = next(
                (s for s in build.sections if str(s.section_id) == str(payload["section_id"])), None
            )
            if section and section.title_path:
                title = " / ".join(section.title_path)[:200]
                values = sources[key]["sections"]
                if title not in values and len(values) < 6:
                    values.append(title)
                break
    return tuple(sources.values())
