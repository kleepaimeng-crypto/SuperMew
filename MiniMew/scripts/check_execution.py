"""在隔离临时 PostgreSQL 中注入 fake Provider，调用正式 RunExecutor 进行验收。"""

import asyncio

from check_models import main

if __name__ == "__main__":
    asyncio.run(main(test_files=["tests/test_execution_pg.py", "tests/test_events_pg.py"]))
