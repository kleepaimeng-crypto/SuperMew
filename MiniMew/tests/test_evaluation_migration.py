"""0017→0018保留已有聊天事实；head元数据无漂移。"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from minimew.database import create_engine
from minimew.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_models import migrate
from check_runtime import temporary_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned temporary PG required"
)


def test_evaluation_upgrade_downgrade_preserves_existing_user_and_thread():
    async def scenario():
        async with temporary_database(revision="0017") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            try:
                async with engine.begin() as conn:
                    user = await conn.scalar(
                        text(
                            "INSERT INTO users(username,password_hash) "
                            "VALUES('pre_evaluation','fixture') RETURNING id"
                        )
                    )
                    await conn.execute(
                        text("INSERT INTO threads(user_id,title) VALUES(:user,'旧对话')"),
                        {"user": user},
                    )

                async def facts():
                    async with engine.connect() as conn:
                        return {
                            table: (await conn.execute(text(f"SELECT to_jsonb(t) FROM {table} t")))
                            .scalars()
                            .all()
                            for table in ("users", "threads")
                        }

                before = await facts()
                migrate(database, "upgrade", "head")
                migrate(database, "check")
                assert await facts() == before
                migrate(database, "downgrade", "0017")
                assert await facts() == before
                migrate(database, "upgrade", "head")
            finally:
                await engine.dispose()

    asyncio.run(scenario())
