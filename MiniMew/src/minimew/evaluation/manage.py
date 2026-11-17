"""显式评测资料准备/精确清理命令，状态仍以PG为准。"""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.documents.storage import SourceStore
from minimew.evaluation.corpus import cleanup, prepare
from minimew.evaluation.protocol import Dataset, load
from minimew.settings import Settings
from minimew.vector_store.config import VectorSettings


async def execute(args):
    settings = Settings()
    engine = create_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if args.command == "prepare":
            dataset = load(args.dataset, Dataset)
            mapping = {
                k: UUID(v) for k, v in json.loads(args.mapping.read_text(encoding="utf-8")).items()
            }
            corpus_id = await prepare(
                sessions,
                args.user,
                dataset,
                mapping,
                SourceStore(settings.document_upload_dir),
                VectorSettings(),
            )
            print(json.dumps({"corpus_id": str(corpus_id)}))
        else:
            await cleanup(sessions, args.user, args.corpus, VectorSettings())
            print('{"status":"cleaned"}')
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "cleanup"))
    parser.add_argument("--user", type=UUID, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--corpus", type=UUID)
    args = parser.parse_args()
    if args.command == "prepare" and (args.dataset is None or args.mapping is None):
        parser.error("prepare requires --dataset and --mapping")
    if args.command == "cleanup" and args.corpus is None:
        parser.error("cleanup requires --corpus")
    try:
        asyncio.run(execute(args))
    except Exception:
        # 不回显输入资料、DSN或第三方异常；未就绪产物可从PG归属记录精确定位。
        print('{"code":"EVALUATION_MANAGEMENT_FAILED"}')
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
