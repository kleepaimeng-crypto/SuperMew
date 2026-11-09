"""管理命令：默认只列出孤立文件；仅在持锁并确认 DB 无版本记录后精确删除。"""

import argparse
import asyncio
import time
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.concurrency import run_in_threadpool

from minimew.database import create_engine
from minimew.documents.catalog import source_lock
from minimew.documents.storage import SourceStore
from minimew.models import DocumentVersion
from minimew.settings import Settings


async def clean_orphans(sessions, store, *, minimum_age_seconds=3600, apply=False):
    if minimum_age_seconds < 0:
        raise ValueError("Invalid age")
    candidates = []
    if not store.root.exists():
        return candidates
    for path in store.root.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix != ".source":
            continue
        try:
            version_id = UUID(path.stem)
        except ValueError:
            continue
        if path.name != f"{version_id}.source":
            continue
        async with sessions() as session, session.begin():
            await source_lock(session, version_id)
            if await session.get(DocumentVersion, version_id) is not None:
                continue
            if not path.exists() or time.time() - path.stat().st_mtime < minimum_age_seconds:
                continue
            candidates.append(version_id)
            if apply:
                await run_in_threadpool(store.remove_orphan, version_id)
    return candidates


async def main(args):
    settings = Settings()
    engine = create_engine(settings)
    try:
        result = await clean_orphans(
            async_sessionmaker(engine),
            SourceStore(settings.document_upload_dir),
            minimum_age_seconds=args.min_age_seconds,
            apply=args.apply,
        )
        for version_id in result:
            print(version_id)
        print(f"{'Removed' if args.apply else 'Found'} {len(result)} unreferenced sources")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--min-age-seconds", type=float, default=3600)
    asyncio.run(main(parser.parse_args()))
