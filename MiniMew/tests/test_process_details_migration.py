"""0020旧过程不改写；0021元信息沿用Journal且有历史时拒绝降级。"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from execution_helpers import activate_claim
from historical_runs import reserve_legacy_run
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.conversation_schemas import CreateRunRequest
from minimew.database import create_engine
from minimew.execution.process import ProcessReporter
from minimew.execution.repository import ExecutionRepository
from minimew.model_control import set_current_model
from minimew.models import Thread, User
from minimew.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_models import migrate  # noqa: E402
from check_runtime import temporary_database  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


def test_old_process_journal_survives_upgrade_and_detail_history_blocks_downgrade(
    model_configuration,
):
    async def scenario():
        async with temporary_database(revision="0020") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions() as session, session.begin():
                    user = User(username="before_process_details", password_hash="fixture")
                    session.add(user)
                    await session.flush()
                    thread = Thread(user_id=user.id, title="旧过程")
                    session.add(thread)
                    await session.flush()
                async with sessions() as session:
                    await set_current_model(session, "before-details", model_configuration)
                async with sessions() as session:
                    run, _ = await reserve_legacy_run(
                        session,
                        user.id,
                        thread.id,
                        CreateRunRequest(idempotency_key="old-process", content="准备工作"),
                    )
                repo = ExecutionRepository(sessions)
                claim = await repo.claim(user.id, thread.id, UUID(str(run.id)))
                await activate_claim(repo, claim)

                async def sink(data):
                    assert await repo.record_process(claim, data)

                async with ProcessReporter(run.id, sink).step("model", "old"):
                    pass

                async def snapshot():
                    async with engine.connect() as connection:
                        return (
                            (
                                await connection.execute(
                                    text("SELECT to_jsonb(e) FROM run_events e ORDER BY sequence")
                                )
                            )
                            .scalars()
                            .all()
                        )

                before = await snapshot()
                migrate(database, "upgrade", "head")
                migrate(database, "check")
                assert await snapshot() == before
                migrate(database, "downgrade", "0020")
                assert await snapshot() == before
                migrate(database, "upgrade", "head")

                async def detail_sink(data):
                    assert await repo.record_process(claim, data, detail=True)

                async with ProcessReporter(run.id, sink, detail_sink).step(
                    "assessment", "new", phase="initial"
                ) as step:
                    step.finish(outcome="insufficient", missing=["缺少备份路径"])
                await repo.finish(claim, content="done")
                with pytest.raises(subprocess.CalledProcessError):
                    migrate(database, "downgrade", "0020")
                assert (await snapshot())[: len(before)] == before
            finally:
                await engine.dispose()

    asyncio.run(scenario())
