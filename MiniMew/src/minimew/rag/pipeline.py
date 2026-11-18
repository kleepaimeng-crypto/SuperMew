"""同一已发布 scope 内的唯一策略编排；授权和证据登记留在知识工具。"""

import math
import time
from dataclasses import replace

from minimew.documents.retrieval import scoped_index, search_published
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
from minimew.vector_store.manifest import PROFILE


class RagPipeline:
    def __init__(self, sessions, runtime, *, retriever=search_published):
        self.sessions, self.runtime, self.retriever = sessions, runtime, retriever

    async def search(self, query, scope, user_id, policy=None, *, deadline=None):
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
        vector = await runtime.sync(runtime.encoder.encode_query, query)
        hybrid = policy.strategy == "hybrid" and all(
            "bm25" in runtime.index.capabilities(m) for m in scope.targets
        )
        if policy.strategy == "hybrid" and not hybrid:
            trace.fallback = "HYBRID_LAYOUT_UNAVAILABLE"
        hits = await self.retriever(
            self.sessions,
            runtime.index,
            scope,
            user_id,
            vector,
            PROFILE,
            top_k=policy.recall_k if hybrid else 3,
            offload=runtime.sync,
        )
        trace.stages = {"dense_count": len(hits), "seconds": time.monotonic() - started}
        if hybrid:
            lexical = await self.retriever(
                self.sessions,
                runtime.index,
                scope,
                user_id,
                vector,
                PROFILE,
                top_k=policy.recall_k,
                offload=runtime.sync,
                lexical_query=query,
            )
            candidates = fuse(hits, lexical, policy)
            trace.stages.update(bm25_count=len(lexical), fused_count=len(candidates))
            groups = await complete(candidates, scope, runtime, policy, trace)
            if policy.rerank and candidates:
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
            trace.stages["seconds"] = time.monotonic() - started
            return RetrievalResult(select(groups, policy.final_k, trace), trace)
        return RetrievalResult(
            tuple(MaterialGroup((Candidate(row, dense=row["score"]),)) for row in hits), trace
        )
