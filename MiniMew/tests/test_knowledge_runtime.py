"""资源生命周期/线程准入；不加载模型或访问网络。"""

import asyncio
import threading
from types import SimpleNamespace

import pytest

import minimew.knowledge_runtime as runtime_module
from minimew.embeddings.errors import EmbeddingError
from minimew.execution.tools import ToolFailure
from minimew.knowledge_runtime import KnowledgeRuntime, knowledge_lifespan
from minimew.rag.reranker import RerankError
from minimew.vector_store.manifest import VectorError


def test_disabled_mode_never_constructs_or_loads_optional_resources(monkeypatch):
    def forbidden():
        raise AssertionError("Disabled mode must not initialize knowledge")

    monkeypatch.setattr(runtime_module, "KnowledgeRuntime", forbidden)

    async def scenario():
        async with knowledge_lifespan(object(), SimpleNamespace(knowledge_enabled=False)) as value:
            assert value is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure,code",
    [
        (EmbeddingError("private"), "KNOWLEDGE_ENCODING_FAILED"),
        (VectorError("private"), "KNOWLEDGE_RETRIEVAL_FAILED"),
        (RerankError("RERANK_NOT_PREPARED"), "KNOWLEDGE_RETRIEVAL_FAILED"),
    ],
)
def test_startup_failure_is_explicit_and_partial_client_is_closed(monkeypatch, failure, code):
    closed = []

    def load(self):
        self.backend = SimpleNamespace(close=lambda: closed.append(threading.get_ident()))
        raise failure

    monkeypatch.setattr(KnowledgeRuntime, "_load", load)

    async def scenario():
        with pytest.raises(ToolFailure) as error:
            async with knowledge_lifespan(object(), SimpleNamespace(knowledge_enabled=True)):
                raise AssertionError("Must not serve with missing resources")
        assert str(error.value) == code and len(closed) == 1

    asyncio.run(scenario())


def test_cancelling_waiter_does_not_release_busy_thread_or_execute_cancelled_queue():
    async def scenario():
        runtime = KnowledgeRuntime()
        entered, release = threading.Event(), threading.Event()
        calls = []

        def first():
            calls.append("first")
            entered.set()
            assert release.wait(3)

        def second():
            calls.append("second")

        try:
            task = asyncio.create_task(runtime.sync(first))
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            queued = asyncio.create_task(runtime.sync(second))
            await asyncio.sleep(0.02)
            assert calls == ["first"]
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            release.set()
            assert await runtime.sync(lambda: "next") == "next"
            assert calls == ["first"]
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())


def test_close_follows_inflight_operation_on_same_thread():
    async def scenario():
        runtime = KnowledgeRuntime()
        order, entered, release = [], threading.Event(), threading.Event()
        runtime.backend = SimpleNamespace(
            close=lambda: order.append(("close", threading.get_ident()))
        )

        def work():
            entered.set()
            assert release.wait(3)
            order.append(("work", threading.get_ident()))

        task = asyncio.create_task(runtime.sync(work))
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(runtime.aclose())
        await asyncio.sleep(0.01)
        assert not closing.done() and not order
        release.set()
        await task
        await closing
        assert [v[0] for v in order] == ["work", "close"] and order[0][1] == order[1][1]
        with pytest.raises(ToolFailure):
            await runtime.sync(lambda: None)

    asyncio.run(scenario())


def test_vector_probe_is_read_only_and_disables_implicit_retry():
    from unittest.mock import Mock

    from minimew.vector_store.config import VectorSettings
    from minimew.vector_store.milvus import MilvusBackend

    client = SimpleNamespace(list_collections=Mock(return_value=[]), create_collection=Mock())
    MilvusBackend(VectorSettings(), client).probe()
    client.list_collections.assert_called_once_with(
        timeout=None, retry_times=0, retry_on_rate_limit=False
    )
    client.create_collection.assert_not_called()
