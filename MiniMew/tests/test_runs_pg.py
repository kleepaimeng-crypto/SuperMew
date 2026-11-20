"""只在 check_models 显式创建的临时 PostgreSQL 内验证提交与并发。"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from minimew.app import create_app
from minimew.database import create_engine
from minimew.model_control import set_current_model, snapshot_for_run
from minimew.models import Message, Run, Thread
from minimew.settings import Settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"), reason="Explicit temporary PostgreSQL required"
)


@pytest.fixture
def client(monkeypatch, model_configuration):
    database = os.environ["MINIMEW_INTEGRATION_DB"]
    assert (
        database.startswith("minimew_test_") and len(database.removeprefix("minimew_test_")) == 32
    )
    monkeypatch.setenv("MINIMEW_DB_NAME", database)
    asyncio.run(configure_model("test-" + uuid4().hex, model_configuration))
    with TestClient(create_app()) as client:
        yield client


async def configure_model(name, configuration):
    engine = create_engine(Settings())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await set_current_model(session, name, configuration)
    finally:
        await engine.dispose()


async def run_snapshot(run_id):
    engine = create_engine(Settings())
    try:
        async with async_sessionmaker(engine)() as session:
            return snapshot_for_run(await session.get(Run, UUID(run_id)))
    finally:
        await engine.dispose()


def account(client):
    credentials = {"username": "run_" + uuid4().hex[:20], "password": "run-test-password-1234"}
    assert client.post("/auth/register", json=credentials).status_code == 201
    login = client.post("/auth/login", json=credentials)
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    thread = client.post("/v1/threads", json={}, headers=headers)
    assert thread.status_code == 201
    return headers, thread.json()["id"]


def submit(client, headers, thread, key="key", content="解释一下 RAG"):
    return client.post(
        f"/v1/threads/{thread}/runs",
        headers=headers,
        json={"idempotency_key": key, "content": content},
    )


async def saved(thread):
    engine = create_engine(Settings())
    try:
        async with engine.connect() as conn:
            thread_id = UUID(thread)
            row = (
                await conn.execute(
                    select(Thread.title, Thread.last_sequence).where(Thread.id == thread_id)
                )
            ).one()
            runs = await conn.scalar(
                select(func.count()).select_from(Run).where(Run.thread_id == thread_id)
            )
            messages = await conn.scalar(
                select(func.count()).select_from(Message).where(Message.thread_id == thread_id)
            )
            return row.title, row.last_sequence, runs, messages
    finally:
        await engine.dispose()


def test_atomic_pair_replay_conflict_and_first_title(client):
    headers, thread = account(client)
    content = "  第一条\n  用户消息  "
    first = submit(client, headers, thread, content=content)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["status"] == "pending"
    assert body["user_message_id"] != body["assistant_message_id"]
    assert first.headers["cache-control"] == "no-store"
    assert asyncio.run(saved(thread)) == ("第一条 用户消息", 2, 1, 2)
    with TestClient(create_app()) as second:
        replay = submit(second, headers, thread, content=content)
        assert replay.status_code == 200 and replay.json() == body
        assert second.get(f"/v1/threads/{thread}/runs/{body['id']}", headers=headers).json() == body
    assert submit(client, headers, thread, content="different").status_code == 409
    assert asyncio.run(saved(thread)) == ("第一条 用户消息", 2, 1, 2)
    assert submit(client, headers, thread, key="next", content="第二条").status_code == 201
    assert asyncio.run(saved(thread)) == ("第一条 用户消息", 4, 2, 4)
    history = client.get(f"/v1/threads/{thread}/messages", headers=headers).json()["items"]
    assert [m["sequence"] for m in history] == [1, 2, 3, 4]
    assert history[0]["content"] == content
    assert history[1]["content"] == "" and history[1]["status"] == "pending"
    assert history[0]["status"] == "completed"
    assert history[0]["id"] == body["user_message_id"]


def test_parallel_same_key_two_apps_and_distinct_keys(client):
    headers, thread = account(client)
    gate = Barrier(2)
    with TestClient(create_app()) as second:

        def send(which):
            gate.wait(timeout=5)
            return submit(which, headers, thread)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, [client, second]))
    assert sorted(r.status_code for r in results) == [200, 201]
    assert results[0].json() == results[1].json()
    assert asyncio.run(saved(thread))[1:] == (2, 1, 2)
    gate = Barrier(2)

    def next_run(key):
        gate.wait(timeout=5)
        return submit(client, headers, thread, key=key, content=key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(next_run, ["next-a", "next-b"]))
    assert all(r.status_code == 201 for r in results)
    assert asyncio.run(saved(thread))[1:] == (6, 3, 6)
    messages = client.get(f"/v1/threads/{thread}/messages", headers=headers).json()["items"]
    assert [m["sequence"] for m in messages] == list(range(1, 7))


def test_history_cursor_and_ownership(client):
    headers, thread = account(client)
    stranger, other_thread = account(client)
    for i in range(3):
        assert submit(client, headers, thread, key=str(i)).status_code == 201
    page = client.get(f"/v1/threads/{thread}/messages?limit=3", headers=headers).json()
    assert [m["sequence"] for m in page["items"]] == [4, 5, 6]
    assert page["previous_cursor"] == 4
    previous = client.get(f"/v1/threads/{thread}/messages?limit=3&before=4", headers=headers).json()
    assert [m["sequence"] for m in previous["items"]] == [1, 2, 3]
    assert previous["previous_cursor"] is None
    assert client.get(f"/v1/threads/{other_thread}/messages", headers=stranger).json() == {
        "items": [],
        "previous_cursor": None,
    }
    run_id = page["items"][0]["run_id"]
    for suffix in ["messages", f"runs/{run_id}"]:
        assert client.get(f"/v1/threads/{thread}/{suffix}", headers=stranger).status_code == 404
        assert client.get(f"/v1/threads/{uuid4()}/{suffix}", headers=stranger).status_code == 404
    assert submit(client, stranger, thread).status_code == 404
    assert (
        client.get(f"/v1/threads/{other_thread}/runs/{run_id}", headers=stranger).status_code == 404
    )
    assert submit(client, stranger, other_thread, key="0").status_code == 201
    for query in ["limit=0", "limit=101", "before=0"]:
        assert (
            client.get(f"/v1/threads/{thread}/messages?{query}", headers=headers).status_code == 422
        )


def test_failure_after_pair_flush_rolls_back_title_and_retry(client, monkeypatch):
    headers, thread = account(client)
    original = AsyncSession.flush

    async def fail(self, *args, **kwargs):
        has_pair = any(isinstance(obj, Message) for obj in self.new)
        await original(self, *args, **kwargs)
        if has_pair:
            raise RuntimeError("injected after pair flush")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "flush", fail)
        with pytest.raises(RuntimeError, match="injected"):
            submit(client, headers, thread)
    assert asyncio.run(saved(thread)) == ("新对话", 0, 0, 0)
    assert submit(client, headers, thread).status_code == 201
    assert asyncio.run(saved(thread)) == ("解释一下 RAG", 2, 1, 2)


def test_invalid_input_leaves_no_run(client):
    headers, thread = account(client)
    for body in [
        {"content": " "},
        {"content": "hi", "idempotency_key": "k", "user_id": str(uuid4())},
        {"content": "hi", "idempotency_key": "has space"},
    ]:
        assert (
            client.post(f"/v1/threads/{thread}/runs", headers=headers, json=body).status_code == 422
        )
    assert asyncio.run(saved(thread)) == ("新对话", 0, 0, 0)


def test_concurrent_key_payload_conflict(client):
    headers, thread = account(client)
    gate = Barrier(2)

    def send(content):
        gate.wait(timeout=5)
        return submit(client, headers, thread, content=content)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, ["first", "second"]))
    assert sorted(response.status_code for response in results) == [201, 409]
    assert asyncio.run(saved(thread))[1:] == (2, 1, 2)


def test_database_constraints_reject_cross_links_and_duplicates(client):
    from sqlalchemy import insert
    from sqlalchemy.exc import IntegrityError

    headers, thread = account(client)
    _, other_thread = account(client)
    response = submit(client, headers, thread)
    assert response.status_code == 201
    run_id = UUID(response.json()["id"])

    async def check_constraints():
        engine = create_engine(Settings())
        try:
            async with engine.connect() as conn:
                other_user = await conn.scalar(
                    select(Thread.user_id).where(Thread.id == UUID(other_thread))
                )
                snapshot = (
                    await conn.execute(
                        select(
                            Run.model_snapshot_json,
                            Run.model_catalog_hash,
                            Run.skill_request,
                            Run.skill_snapshot,
                        ).where(Run.id == run_id)
                    )
                ).one()
            invalid = [
                (
                    insert(Run).values(
                        thread_id=UUID(thread),
                        user_id=other_user,
                        idempotency_key="foreign",
                        model_snapshot_json=snapshot.model_snapshot_json,
                        model_catalog_hash=snapshot.model_catalog_hash,
                        skill_request=snapshot.skill_request,
                        skill_snapshot=snapshot.skill_snapshot,
                    ),
                    "23503",
                ),
                (
                    insert(Message).values(
                        thread_id=UUID(thread),
                        run_id=run_id,
                        sequence=3,
                        role="user",
                        status="completed",
                        content="duplicate",
                    ),
                    "23505",
                ),
                (
                    insert(Message).values(
                        thread_id=UUID(thread),
                        run_id=run_id,
                        sequence=3,
                        role="system",
                        status="completed",
                        content="invalid role",
                    ),
                    "23514",
                ),
            ]
            for statement, code in invalid:
                with pytest.raises(IntegrityError) as caught:
                    async with engine.begin() as conn:
                        await conn.execute(statement)
                assert caught.value.orig.sqlstate == code
            async with engine.connect() as conn:
                owner = await conn.scalar(select(Thread.user_id).where(Thread.id == UUID(thread)))
                probe_run = await conn.scalar(
                    insert(Run)
                    .values(
                        thread_id=UUID(thread),
                        user_id=owner,
                        idempotency_key="probe",
                        model_snapshot_json=snapshot.model_snapshot_json,
                        model_catalog_hash=snapshot.model_catalog_hash,
                        skill_request=snapshot.skill_request,
                        skill_snapshot=snapshot.skill_snapshot,
                    )
                    .returning(Run.id)
                )
                with pytest.raises(IntegrityError) as caught:
                    async with conn.begin_nested():
                        await conn.execute(
                            insert(Message).values(
                                thread_id=UUID(other_thread),
                                run_id=probe_run,
                                sequence=1,
                                role="user",
                                status="completed",
                                content="foreign",
                            )
                        )
                assert caught.value.orig.sqlstate == "23503"
                await conn.rollback()  # 连探针 Run 一起撤销，不留下不完整记录。
        finally:
            await engine.dispose()

    asyncio.run(check_constraints())
    assert asyncio.run(saved(thread))[1:] == (2, 1, 2)


def account_model(client, headers, name, configuration, identity=None):
    # 0023后聊天从账户项/.env解析；保留原冻结/版本/幂等断言，设置走正式账户接口。
    from minimew.account_models.schemas import ModelWrite
    from minimew.model_control import build_snapshot

    data = ModelWrite(
        name=name,
        adapter="generic",
        model_name=configuration.model_name,
        base_url=configuration.base_url,
        max_output_tokens=configuration.max_output_tokens,
        timeout_seconds=configuration.timeout_seconds,
        temperature=configuration.temperature,
        api_key=None if identity else "test-account-key",
    )
    path = "/v1/account/model-settings/models" + (f"/{identity}" if identity else "")
    response = client.request(
        "PUT" if identity else "POST",
        path,
        headers=headers,
        json=data.model_dump(mode="json", exclude={"api_key"})
        | {"api_key": None if identity else "test-account-key"},
    )
    assert response.status_code == (200 if identity else 201)
    value = response.json()
    assert (
        client.put(
            "/v1/account/model-settings/selection",
            headers=headers,
            json=dict(model_id=value["id"], knowledge_enabled=True),
        ).status_code
        == 204
    )
    return build_snapshot(data.configuration(), UUID(value["id"]), value["revision"])


def test_profile_change_freezes_old_run_and_replay_uses_original(client, model_configuration):
    name = "snapshot-" + uuid4().hex
    headers, thread = account(client)
    original = account_model(client, headers, name, model_configuration)
    first = submit(client, headers, thread)
    assert first.status_code == 201
    before = asyncio.run(run_snapshot(first.json()["id"]))
    assert before == original
    changed = model_configuration.model_copy(
        update={"model_name": "test-model-v2", "temperature": 0.8}
    )
    updated = account_model(client, headers, name, changed, original.answer.profile_id)
    assert updated.answer.profile_id == original.answer.profile_id
    assert updated.answer.profile_version == original.answer.profile_version + 1
    assert updated.catalog_hash() != original.catalog_hash()
    assert submit(client, headers, thread).json() == first.json()
    assert submit(client, headers, thread).status_code == 200
    assert asyncio.run(run_snapshot(first.json()["id"])) == original
    second = submit(client, headers, thread, key="next")
    assert second.status_code == 201
    assert asyncio.run(run_snapshot(second.json()["id"])) == updated
    assert (
        account_model(
            client, headers, name, updated.answer.configuration, updated.answer.profile_id
        )
        == updated
    )
    assert "base_url" not in first.text and "model_snapshot" not in first.text


def test_missing_or_invalid_current_config_rolls_back_but_replay_still_works(
    client, model_configuration
):
    from sqlalchemy import update

    from minimew.models import AccountModelConfiguration

    headers, thread = account(client)
    selected = account_model(client, headers, "invalid-fixture", model_configuration)
    original = submit(client, headers, thread)
    assert original.status_code == 201
    empty_thread = client.post("/v1/threads", json={}, headers=headers).json()["id"]

    async def invalidate(*, missing):
        engine = create_engine(Settings())
        try:
            async with engine.begin() as conn:
                values = (
                    {"deleted_at": func.now()}
                    if missing
                    else {"configuration": {"api_key": "private-test-secret"}}
                )
                await conn.execute(
                    update(AccountModelConfiguration)
                    .where(AccountModelConfiguration.id == selected.answer.profile_id)
                    .values(**values)
                )
        finally:
            await engine.dispose()

    for missing in [False, True]:
        asyncio.run(invalidate(missing=missing))
        assert submit(client, headers, thread).json() == original.json()
        assert submit(client, headers, thread).status_code == 200
        assert submit(client, headers, thread, content="changed").status_code == 409
        failure = submit(client, headers, empty_thread)
        assert failure.status_code == 503
        assert failure.json() == {"detail": {"code": "MODEL_CONFIGURATION_UNAVAILABLE"}}
        assert "private-test-secret" not in failure.text
        assert asyncio.run(saved(empty_thread)) == ("新对话", 0, 0, 0)


def test_database_rejects_missing_or_changed_run_snapshot(client):
    from sqlalchemy import insert, update
    from sqlalchemy.exc import IntegrityError

    headers, thread = account(client)
    result = submit(client, headers, thread)
    run_id = UUID(result.json()["id"])

    async def verify():
        engine = create_engine(Settings())
        try:
            async with engine.connect() as conn:
                owner = await conn.scalar(select(Thread.user_id).where(Thread.id == UUID(thread)))
            statements = [
                update(Run).where(Run.id == run_id).values(model_catalog_hash="f" * 64),
                update(Run).where(Run.id == run_id).values(model_snapshot_json={}),
                update(Run)
                .where(Run.id == run_id)
                .values(model_snapshot_json=None, model_catalog_hash=None),
                insert(Run).values(
                    thread_id=UUID(thread), user_id=owner, idempotency_key="no-snapshot"
                ),
            ]
            for statement in statements:
                with pytest.raises(IntegrityError) as caught:
                    async with engine.begin() as conn:
                        await conn.execute(statement)
                assert caught.value.orig.sqlstate == "23514"
        finally:
            await engine.dispose()

    asyncio.run(verify())
    assert asyncio.run(run_snapshot(result.json()["id"]))
    assert asyncio.run(saved(thread))[1:] == (2, 1, 2)


def test_model_update_failure_rolls_back_configuration_version_and_assignment(
    client, monkeypatch, model_configuration
):
    from minimew.model_control import freeze_current_snapshot
    from minimew.models import ModelProfile

    name = "rollback-" + uuid4().hex
    before = asyncio.run(configure_model(name, model_configuration))
    original_flush = AsyncSession.flush

    async def fail(self, *args, **kwargs):
        changed = any(isinstance(obj, ModelProfile) for obj in self.dirty)
        await original_flush(self, *args, **kwargs)
        if changed:
            raise RuntimeError("injected model update failure")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "flush", fail)
        with pytest.raises(RuntimeError, match="injected model update"):
            asyncio.run(
                configure_model(name, model_configuration.model_copy(update={"temperature": 0.8}))
            )

    async def current():
        engine = create_engine(Settings())
        try:
            async with async_sessionmaker(engine)() as session, session.begin():
                return await freeze_current_snapshot(session)
        finally:
            await engine.dispose()

    assert asyncio.run(current()) == before


def test_concurrent_model_updates_have_monotonic_versions(client, model_configuration):
    name = "concurrent-" + uuid4().hex
    headers, thread = account(client)
    initial = account_model(client, headers, name, model_configuration)
    gate = Barrier(2)

    def update_model(temperature):
        gate.wait(timeout=5)
        return account_model(
            client,
            headers,
            name,
            model_configuration.model_copy(update={"temperature": temperature}),
            initial.answer.profile_id,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update_model, [0.4, 0.8]))
    assert sorted(item.answer.profile_version for item in results) == [
        initial.answer.profile_version + 1,
        initial.answer.profile_version + 2,
    ]
    response = submit(client, headers, thread)
    assert response.status_code == 201
    assert asyncio.run(run_snapshot(response.json()["id"])) == max(
        results, key=lambda item: item.answer.profile_version
    )


def test_local_management_cli_sets_and_reads_snapshot(client, tmp_path, model_configuration):
    import json
    import subprocess
    import sys

    source = tmp_path / "model.json"
    source.write_text(model_configuration.model_dump_json(), encoding="utf-8")
    name = "cli-" + uuid4().hex

    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "minimew.model_cli", *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        return json.loads(result.stdout)

    first = cli("set", "--name", name, "--file", str(source))
    assert cli("show") == first
    headers, thread = account(client)
    created = submit(client, headers, thread)
    assert created.status_code == 201
    snapshot = asyncio.run(run_snapshot(created.json()["id"]))
    assert cli("show-run", created.json()["id"]) == {
        "model_catalog_hash": snapshot.catalog_hash(),
        "model_snapshot": snapshot.model_dump(mode="json"),
    }
    # 历史Profile CLI不再作为账户聊天的默认事实来源。
    assert snapshot.answer.configuration.model_name == Settings().default_model_name
    assert snapshot.answer.profile_id != UUID(first["model_snapshot"]["answer"]["profile_id"])


def test_config_update_during_reservation_does_not_mix_snapshots(
    client, monkeypatch, model_configuration
):
    from threading import Event

    import minimew.account_models.service as control
    import minimew.conversations as conversations

    name = "race-" + uuid4().hex
    headers, thread = account(client)
    before = account_model(client, headers, name, model_configuration)
    captured, update_started, release = Event(), Event(), Event()
    original_freeze = conversations.freeze_account
    original_preference = control.preference

    async def pause_after_freeze(*args, **kwargs):
        snapshot = await original_freeze(*args, **kwargs)
        captured.set()
        if not await asyncio.to_thread(release.wait, 10):
            raise TimeoutError("test reservation gate")
        return snapshot

    async def observe_update(*args, **kwargs):
        if captured.is_set():
            update_started.set()
        return await original_preference(*args, **kwargs)

    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as pool:
        patch.setattr(conversations, "freeze_account", pause_after_freeze)
        patch.setattr(control, "preference", observe_update)
        request = pool.submit(submit, client, headers, thread)
        try:
            assert captured.wait(5)
            update = pool.submit(
                account_model,
                client,
                headers,
                name,
                model_configuration.model_copy(update={"temperature": 0.8}),
                before.answer.profile_id,
            )
            assert update_started.wait(5)
        finally:
            release.set()
        response = request.result(timeout=10)
        after = update.result(timeout=10)
    assert response.status_code == 201
    assert asyncio.run(run_snapshot(response.json()["id"])) == before
    next_run = submit(client, headers, thread, key="after-update")
    assert next_run.status_code == 201
    assert asyncio.run(run_snapshot(next_run.json()["id"])) == after
