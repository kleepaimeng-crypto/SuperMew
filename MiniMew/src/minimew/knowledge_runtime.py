"""API拥有的本地编码/只读向量资源；一次一个同步操作，调用取消后仍占槽直到完成。"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial

from minimew.embeddings.adapter import LocalEmbeddingAdapter
from minimew.embeddings.config import EmbeddingRuntimeSettings
from minimew.embeddings.errors import EmbeddingError
from minimew.execution.tools import ToolFailure
from minimew.knowledge import KnowledgeService
from minimew.vector_store.adapter import CandidateIndex
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.manifest import PROFILE, VectorError
from minimew.vector_store.milvus import MilvusBackend

logger = logging.getLogger(__name__)


class KnowledgeRuntime:
    def __init__(self, embedding_settings=None, vector_settings=None):
        self.embedding_settings = embedding_settings or EmbeddingRuntimeSettings()
        self.vector_settings = vector_settings or VectorSettings()
        self.encoder = self.backend = self.index = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="minimew-knowledge")
        self._slot = asyncio.Semaphore(1)
        self._closed = False

    def _load(self):
        self.encoder = LocalEmbeddingAdapter.load_cpu(PROFILE, self.embedding_settings)
        self.backend = MilvusBackend.connect(self.vector_settings)
        self.backend.probe()
        self.index = CandidateIndex(self.backend, self.vector_settings.batch_size)

    async def start(self):
        try:
            await self.sync(self._load)
        except EmbeddingError:
            raise ToolFailure("KNOWLEDGE_ENCODING_FAILED") from None
        except VectorError:
            raise ToolFailure("KNOWLEDGE_RETRIEVAL_FAILED") from None

    async def sync(self, operation, *args, **kwargs):
        await self._slot.acquire()
        if self._closed:
            self._slot.release()
            raise ToolFailure("KNOWLEDGE_RETRIEVAL_FAILED")
        try:
            future = asyncio.get_running_loop().run_in_executor(
                self._pool, partial(operation, *args, **kwargs)
            )
        except BaseException:
            self._slot.release()
            raise

        def completed(done):
            self._slot.release()
            if not done.cancelled():
                done.exception()  # 原调用取消后也收集异常，不记录原始SDK/输入内容。

        future.add_done_callback(completed)
        # 线程不能靠取消await强停；保留槽直到真正完成，后续Run等待时可取消。
        return await asyncio.shield(future)

    async def aclose(self):
        if self._closed:
            return
        self._closed = True

        def close():
            if self.backend is not None:
                self.backend.close()
            self.encoder = self.index = self.backend = None

        # 单线程FIFO保证close排在已开始的操作后，不能并发关闭正在使用的连接。
        future = asyncio.get_running_loop().run_in_executor(self._pool, close)
        try:
            await asyncio.wait_for(
                asyncio.shield(future), min(30, self.vector_settings.timeout_seconds + 3)
            )
        except Exception:
            logger.warning("knowledge_cleanup_pending code=KNOWLEDGE_RETRIEVAL_FAILED")
            future.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )
        finally:
            self._pool.shutdown(wait=False, cancel_futures=False)


@asynccontextmanager
async def knowledge_lifespan(sessions, settings):
    if not settings.knowledge_enabled:
        yield None
        return
    runtime = KnowledgeRuntime()
    try:
        await runtime.start()
        yield KnowledgeService(sessions, runtime)
    finally:
        await runtime.aclose()
