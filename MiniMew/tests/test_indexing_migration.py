"""0011 真实已上传文档/任务升级为 0012，内容和旧身份保持不变。"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.database import create_engine
from minimew.documents.catalog import reserve_document
from minimew.documents.storage import SourceStore, validate_source
from minimew.models import User
from minimew.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_models import migrate  # noqa: E402
from check_runtime import temporary_database  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"),
    reason="Explicit owned temporary PostgreSQL required",
)


def test_0011_upload_survives_0012_upgrade_with_original_job_and_source(tmp_path):
    async def scenario():
        async with temporary_database(revision="0011") as database:
            engine = create_engine(Settings().model_copy(update={"db_name": database}))
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            store = SourceStore(tmp_path)
            source = validate_source("before.txt", b"original pending content", 100)
            try:
                # 用 0011 当时的列插入 fixture，当前 ORM 已有新列，不能用新版 reserve 假装旧库。
                async with sessions() as session, session.begin():
                    user = User(username="legacy_index_owner", password_hash="fixture-no-login")
                    session.add(user)
                    await session.flush()
                    owner = user.id
                # 先升前仅目录函数的 ORM IndexJob RETURNING 会读新列，故以旧表 SQL 建立快照。
                import json
                from uuid import uuid4

                from minimew.documents.profiles import preset

                kb, doc, version, job = [uuid4() for _ in range(4)]
                build = preset("bge-small-zh-v1.5").model_copy(update={"source_format": "text"})
                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO knowledge_bases(id,user_id,build_configuration,"
                            "build_fingerprint) VALUES (:kb,:owner,CAST(:build AS jsonb),:fp)"
                        ),
                        dict(
                            kb=kb,
                            owner=owner,
                            build=json.dumps(build.model_dump(mode="json")),
                            fp=build.fingerprint(),
                        ),
                    )
                    await session.execute(
                        text(
                            "INSERT INTO documents(id,user_id,knowledge_base_id,title,"
                            "pending_version_id,last_version_number,publication_fence) "
                            "VALUES (:doc,:owner,:kb,'before',:v,1,1)"
                        ),
                        dict(doc=doc, owner=owner, kb=kb, v=version),
                    )
                    await session.execute(
                        text(
                            "INSERT INTO document_versions(id,document_id,knowledge_base_id,"
                            "user_id,version_number,publication_fence,source_filename,"
                            "source_format,source_size,content_sha256,build_configuration,"
                            "build_fingerprint) VALUES (:v,:doc,:kb,:owner,1,1,'before.txt',"
                            "'text',:size,:sha,CAST(:build AS jsonb),:fp)"
                        ),
                        dict(
                            v=version,
                            doc=doc,
                            kb=kb,
                            owner=owner,
                            size=len(source.content),
                            sha=source.sha256,
                            build=json.dumps(build.model_dump(mode="json")),
                            fp=build.fingerprint(),
                        ),
                    )
                    await session.execute(
                        text(
                            "INSERT INTO index_jobs(id,version_id,document_id,knowledge_base_id,"
                            "user_id) VALUES (:j,:v,:doc,:kb,:owner)"
                        ),
                        dict(j=job, v=version, doc=doc, kb=kb, owner=owner),
                    )
                    store.write(version, source)
                tables = [
                    "users",
                    "knowledge_bases",
                    "documents",
                    "document_versions",
                    "index_jobs",
                ]
                async with engine.connect() as connection:
                    before = {
                        table: (
                            await connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t"))
                        )
                        .scalars()
                        .all()
                        for table in tables
                    }
                migrate(database, "upgrade", "head")
                migrate(database, "check")
                async with engine.connect() as connection:
                    assert (
                        await connection.scalar(text("SELECT version_num FROM alembic_version"))
                        == "0013"
                    )
                    for table in tables:
                        expression = (
                            "to_jsonb(t) - 'next_retry_at' - 'last_error_code'"
                            if table == "index_jobs"
                            else "to_jsonb(t)"
                        )
                        after = (
                            (await connection.execute(text(f"SELECT {expression} FROM {table} t")))
                            .scalars()
                            .all()
                        )
                        assert after == before[table], table
                assert store.read(version, source.sha256) == source.content
                async with sessions() as session:
                    result, created = await reserve_document(
                        session, store, owner, source, "after-upgrade", "bge-small-zh-v1.5", doc
                    )
                    assert not created and result.job.id == job and result.version.id == version
            finally:
                await engine.dispose()

    asyncio.run(scenario())
