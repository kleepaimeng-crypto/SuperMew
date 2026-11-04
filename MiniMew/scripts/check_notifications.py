"""显式使用临时库、独占 Redis 容器和临时 HTTP 端口；结束自动清理。"""

import argparse
import asyncio
import os

from check_models import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tests", nargs="*", default=["tests/test_redis_integration.py"])
    args = parser.parse_args()
    os.environ["MINIMEW_NOTIFICATION_TESTS"] = "1"
    asyncio.run(main(test_files=args.tests))
