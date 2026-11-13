"""同一已发布 scope 内的唯一策略编排；授权和证据登记留在知识工具。"""

import time

from minimew.documents.retrieval import search_published
from minimew.rag.protocol import (
    Candidate,
    MaterialGroup,
    RagPolicy,
    RetrievalResult,
    RetrievalTrace,
)
from minimew.vector_store.manifest import PROFILE


class RagPipeline:
    def __init__(self, sessions, runtime, *, retriever=search_published):
        self.sessions, self.runtime, self.retriever = sessions, runtime, retriever

    async def search(self, query, scope, user_id, policy=None):
        policy = policy or RagPolicy()
        trace = RetrievalTrace(policy.strategy)
        started = time.monotonic()
        vector = await self.runtime.sync(self.runtime.encoder.encode_query, query)
        hits = await self.retriever(
            self.sessions,
            self.runtime.index,
            scope,
            user_id,
            vector,
            PROFILE,
            top_k=3,
            offload=self.runtime.sync,
        )
        trace.stages = {"dense_count": len(hits), "seconds": time.monotonic() - started}
        return RetrievalResult(
            tuple(MaterialGroup((Candidate(row, dense=row["score"]),)) for row in hits), trace
        )
