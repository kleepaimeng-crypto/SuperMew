import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from minimew.rag.pipeline import RagPipeline
from minimew.rag.protocol import RagPolicy


def test_policy_is_strict_bounded_and_frozen():
    with pytest.raises(ValidationError):
        RagPolicy(recall_k=100)
    with pytest.raises(ValidationError):
        RagPolicy(final_k="6")
    with pytest.raises(ValidationError):
        RagPolicy().final_k = 2


def test_pipeline_keeps_scope_and_trace_per_call():
    class Runtime:
        encoder = type("Encoder", (), {"encode_query": lambda self, query: [query]})()
        index = object()

        async def sync(self, fn, *args, **kwargs):
            return fn(*args, **kwargs)

    async def check():
        read = AsyncMock(return_value=[{"chunk_id": "one", "score": 0.3}])
        pipeline = RagPipeline(object(), Runtime(), retriever=read)
        one, two = object(), object()
        user = uuid4()
        a, b = await asyncio.gather(
            pipeline.search("A", one, user), pipeline.search("B", two, user)
        )
        assert a.trace is not b.trace
        assert read.call_args_list[0].args[2] is one
        assert read.call_args_list[1].args[2] is two
        assert a.rows == ({"chunk_id": "one", "score": 0.3},)
        assert a.trace.stages["dense_count"] == 1

    asyncio.run(check())
