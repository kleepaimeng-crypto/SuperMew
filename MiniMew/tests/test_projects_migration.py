"""0018旧事实升级0019；新项目状态禁止静默降级丢失。"""

import asyncio
import os
import subprocess
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
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


def test_projects_upgrade_preserves_personal_data_and_matches_metadata():
    async def scenario():
        async with temporary_database(revision="0018") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            try:
                async with engine.begin() as connection:
                    user = await connection.scalar(
                        text(
                            "INSERT INTO users(username,password_hash) "
                            "VALUES('before_project','fixture') RETURNING id"
                        )
                    )
                    await connection.execute(
                        text("INSERT INTO threads(user_id,title) VALUES(:user,'普通对话')"),
                        {"user": user},
                    )

                async def snapshot():
                    async with engine.connect() as connection:
                        return (
                            await connection.execute(
                                text(
                                    "SELECT id,user_id,title,last_sequence,created_at FROM threads"
                                )
                            )
                        ).all()

                before = await snapshot()
                migrate(database, "upgrade", "head")
                migrate(database, "check")
                assert await snapshot() == before
                async with engine.connect() as connection:
                    assert (
                        await connection.scalar(
                            text("SELECT count(*) FROM threads WHERE project_id IS NOT NULL")
                        )
                        == 0
                    )
                migrate(database, "downgrade", "0018")
                assert await snapshot() == before
                migrate(database, "upgrade", "head")
                async with engine.begin() as connection:
                    kb = await connection.scalar(
                        text(
                            "INSERT INTO knowledge_bases(user_id,build_configuration,"
                            "build_fingerprint) VALUES(:user,'{}',:fingerprint) RETURNING id"
                        ),
                        {"user": user, "fingerprint": "a" * 64},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO documents(user_id,knowledge_base_id,title,deleted_at) "
                            "VALUES(:user,:kb,'已下线资料',now())"
                        ),
                        {"user": user, "kb": kb},
                    )
                with pytest.raises(subprocess.CalledProcessError):
                    migrate(database, "downgrade", "0018")
                async with engine.connect() as connection:
                    assert (
                        await connection.scalar(text("SELECT version_num FROM alembic_version"))
                        == "0019"
                    )
                    assert (
                        await connection.scalar(
                            text("SELECT count(*) FROM documents WHERE deleted_at IS NOT NULL")
                        )
                        == 1
                    )
            finally:
                await engine.dispose()

    asyncio.run(scenario())
