"""本地数据库管理入口；不开放 HTTP 管理权限，也不调用模型服务。"""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.model_control import (
    ModelConfiguration,
    ModelConfigurationUnavailable,
    ModelSnapshotUnavailable,
    ProfileName,
    freeze_current_snapshot,
    set_current_model,
    snapshot_for_run,
)
from minimew.models import Run
from minimew.settings import Settings


async def execute(args):
    # 在连接数据库前校验文件。错误不得回显输入中的疑似 Secret。
    configuration = None
    if args.action == "set":
        ProfileName(name=args.name)
        configuration = ModelConfiguration.model_validate_json(
            args.file.read_text(encoding="utf-8")
        )
    engine = create_engine(Settings())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            if args.action == "set":
                snapshot = await set_current_model(session, args.name, configuration)
            else:
                async with session.begin():
                    if args.action == "show":
                        snapshot = await freeze_current_snapshot(session)
                    else:
                        run = await session.get(Run, args.run_id)
                        if run is None:
                            raise SystemExit("Run not found")
                        snapshot = snapshot_for_run(run)
            print(
                json.dumps(
                    {
                        "model_catalog_hash": snapshot.catalog_hash(),
                        "model_snapshot": snapshot.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    update = commands.add_parser("set", help="维护历史部署Profile；0023后不影响账户聊天")
    update.add_argument("--name", required=True)
    update.add_argument("--file", type=Path, required=True)
    commands.add_parser("show", help="查看历史部署Profile选择；账户聊天默认读取.env")
    inspect_run = commands.add_parser("show-run", help="查看已有 Run 自己的快照")
    inspect_run.add_argument("run_id", type=UUID)
    args = parser.parse_args()
    try:
        asyncio.run(execute(args))
    except (ValidationError, OSError, ValueError):
        raise SystemExit(
            "Invalid configuration or unreadable file; no model update was committed"
        ) from None
    except ModelConfigurationUnavailable:
        raise SystemExit("MODEL_CONFIGURATION_UNAVAILABLE") from None
    except ModelSnapshotUnavailable as error:
        raise SystemExit(error.code) from None
    except SQLAlchemyError:
        raise SystemExit(
            "Database operation failed; check connection and migration revision"
        ) from None


if __name__ == "__main__":
    main()
