"""隔离浏览器验收的进程停止适配；执行仍走正式 documents.worker.main。"""

import argparse
import asyncio
import os
import sys
import threading

from minimew.documents.worker import IndexWorker, main
from minimew.settings import Settings

if __name__ == "__main__":
    settings = Settings()
    assert os.environ.get("MINIMEW_DOCUMENT_BROWSER_TESTS") == "1"
    assert settings.db_name == os.environ["MINIMEW_INTEGRATION_DB"]
    assert settings.db_name.startswith("minimew_test_") and len(settings.db_name) == 45

    serve = IndexWorker.serve

    async def controlled_serve(worker, **kwargs):
        # Windows: 启动前的阻塞stdin读会卡住NumPy扩展导入；模型预热后再接停止通道。
        loop = asyncio.get_running_loop()

        def stop():
            sys.stdin.readline()
            loop.call_soon_threadsafe(worker.stop.set)

        threading.Thread(target=stop, daemon=True).start()
        await serve(worker, **kwargs)

    IndexWorker.serve = controlled_serve
    asyncio.run(main(argparse.Namespace(once=False, requeue_cleanup=None)))
