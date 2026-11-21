"""Upgrade actual 0025 projects without changing model identity or message facts."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from minimew.database import create_engine
from minimew.documents.profiles import preset
from minimew.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_models import migrate
from check_runtime import temporary_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Owned PG required"
)


def test_0025_existing_and_empty_projects_freeze_actual_models():
    async def scenario():
        async with temporary_database(revision="0025") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            try:
                async with engine.begin() as connection:
                    owner = await connection.scalar(
                        text(
                            "INSERT INTO users(username,password_hash) "
                            "VALUES('model_migration','fixture') RETURNING id"
                        )
                    )
                    cloud = await connection.scalar(
                        text("INSERT INTO projects(user_id,name) VALUES(:u,'cloud') RETURNING id"),
                        {"u": owner},
                    )
                    empty = await connection.scalar(
                        text("INSERT INTO projects(user_id,name) VALUES(:u,'empty') RETURNING id"),
                        {"u": owner},
                    )
                    credential = uuid4()
                    rerank_credential = uuid4()
                    await connection.execute(
                        text(
                            "INSERT INTO model_credentials(id,user_id,model_id,encrypted_value) "
                            "VALUES(:id,:u,:p,'fixture')"
                        ),
                        {"id": credential, "u": owner, "p": cloud},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO model_credentials(id,user_id,model_id,encrypted_value) "
                            "VALUES(:id,:u,:p,'fixture')"
                        ),
                        {"id": rerank_credential, "u": owner, "p": cloud},
                    )
                    build = preset("bailian-text-embedding-v4")
                    await connection.execute(
                        text("""INSERT INTO knowledge_bases
                        (user_id,project_id,build_configuration,build_fingerprint,embedding_credential_id,
                         rerank_use_bailian,rerank_credential_id)
                        VALUES(:u,:p,CAST(:build AS jsonb),:fp,:c,true,:r)"""),
                        {
                            "u": owner,
                            "p": cloud,
                            "build": json.dumps(build.model_dump(mode="json")),
                            "fp": build.fingerprint(),
                            "c": credential,
                            "r": rerank_credential,
                        },
                    )
                    tid = await connection.scalar(
                        text(
                            "INSERT INTO threads(user_id,title,project_id) "
                            "VALUES(:u,'保留标题',:p) RETURNING id"
                        ),
                        {"u": owner, "p": cloud},
                    )
                async with engine.begin() as connection:
                    await connection.execute(
                        text("""INSERT INTO embedding_rebuilds
                        (id,knowledge_base_id,user_id,configuration,fingerprint)
                        SELECT :id,id,user_id,build_configuration,build_fingerprint
                        FROM knowledge_bases WHERE project_id=:project"""),
                        {"id": uuid4(), "project": cloud},
                    )
                with pytest.raises(subprocess.CalledProcessError):
                    migrate(database, "upgrade", "head")
                async with engine.begin() as connection:
                    assert (
                        await connection.scalar(text("SELECT version_num FROM alembic_version"))
                        == "0025"
                    )
                    await connection.execute(text("UPDATE embedding_rebuilds SET status='failed'"))
                migrate(database, "upgrade", "head")
                migrate(database, "check")
                async with engine.connect() as connection:
                    rows = (
                        (
                            await connection.execute(
                                text(
                                    "SELECT project_id,project_models,embedding_credential_id,"
                                    "rerank_credential_id,build_fingerprint FROM knowledge_bases"
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                    selected = {r["project_id"]: r for r in rows}
                    assert selected[cloud]["build_fingerprint"] == build.fingerprint()
                    assert selected[cloud]["embedding_credential_id"] == credential
                    assert selected[cloud]["rerank_credential_id"] == rerank_credential
                    endpoints = dict(
                        (
                            await connection.execute(
                                text("SELECT id,retrieval_url FROM model_credentials")
                            )
                        ).all()
                    )
                    assert (
                        endpoints[credential] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
                    )
                    assert (
                        endpoints[rerank_credential]
                        == "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
                    )
                    assert (
                        selected[cloud]["project_models"]["embedding"]["model_name"]
                        == "text-embedding-v4"
                    )
                    assert (
                        selected[cloud]["project_models"]["rerank"]["model_name"] == "qwen3-rerank"
                    )
                    assert (
                        selected[empty]["project_models"]["embedding"]["model_name"]
                        == preset(Settings().embedding_profile).embedding.model_id
                    )
                    thread = (
                        await connection.execute(
                            text("SELECT title,archived FROM threads WHERE id=:id"), {"id": tid}
                        )
                    ).one()
                    assert thread == ("保留标题", False)
                with pytest.raises(subprocess.CalledProcessError):
                    migrate(database, "downgrade", "0025")
            finally:
                await engine.dispose()

    asyncio.run(scenario())
