"""工程包 A：在自有临时 PostgreSQL 验证上传/锁/迁移，保留开发库。"""

import asyncio
import sys

from check_runtime import main

if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1:]
            or [
                "tests/test_documents_pg.py",
                "tests/test_document_migration.py",
                "tests/test_cancellation_migration.py",
                "tests/test_threads_pg.py",
                "tests/test_runs_pg.py",
            ]
        )
    )
