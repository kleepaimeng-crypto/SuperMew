"""显式临时 PostgreSQL 与独占临时 HTTP 端口的 SSE 验收。"""

import asyncio

from check_models import main

if __name__ == "__main__":
    asyncio.run(main(test_files=["tests/test_sse_pg.py", "tests/test_stream_reads_pg.py"]))
