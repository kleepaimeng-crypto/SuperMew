"""只在显式临时 PostgreSQL 中验证待办事务和历史迁移。"""

import asyncio

from check_models import main

if __name__ == "__main__":
    asyncio.run(main(test_files=["tests/test_outbox_pg.py"]))
