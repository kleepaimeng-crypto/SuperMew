"""显式临时 PostgreSQL：真实目录、文件、API、锁竞争；不调用模型。"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from minimew.app import create_app
from minimew.database import create_engine
from minimew.documents.catalog import reserve_document, source_lock
from minimew.documents.cleanup import clean_orphans
from minimew.documents.jobs import IndexJobRepository
from minimew.documents.profiles import preset
from minimew.documents.storage import SourceStore, validate_source
from minimew.models import Document, DocumentVersion, IndexJob, KnowledgeBase
from minimew.settings import Settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"),
    reason="Explicit owned temporary PostgreSQL required",
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    database = os.environ["MINIMEW_INTEGRATION_DB"]
    assert database.startswith("minimew_test_") and len(database) == 45
    monkeypatch.setenv("MINIMEW_DB_NAME", database)
    monkeypatch.setenv("MINIMEW_DOCUMENT_UPLOAD_DIR", str(tmp_path))

    async def reset_owned_test_catalog(sessions):
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "TRUNCATE index_cleanup_jobs,index_artifacts,document_submissions,index_jobs,"
                    "document_versions,documents,knowledge_bases"
                )
            )

    asyncio.run(database_call(reset_owned_test_catalog))
    with TestClient(create_app()) as client:
        yield client


def account(client):
    credentials = {"username": "doc_" + uuid4().hex[:20], "password": "doc-test-password-1234"}
    assert client.post("/auth/register", json=credentials).status_code == 201
    login = client.post("/auth/login", json=credentials)
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    return headers, UUID(user_id)


def upload(client, headers, *, document=None, key="key", content=b"hello", filename="a.md"):
    path = f"/v1/documents/{document}/versions" if document else "/v1/documents"
    return client.post(
        path, headers=headers, data={"idempotency_key": key}, files={"file": (filename, content)}
    )


async def database_call(operation):
    engine = create_engine(Settings())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await operation(sessions)
    finally:
        await engine.dispose()


def test_upload_receipt_survives_new_client_and_replays_original_job(client, tmp_path):
    headers, user = account(client)
    first = upload(client, headers)
    assert first.status_code == 202, first.text
    value = first.json()
    assert value["document"]["current_version_id"] is None
    assert value["document"]["pending_version_id"] == value["version"]["id"]
    assert value["job"]["status"] == "pending"
    assert value["version"]["build_configuration"]["embedding"]["dimension"] == 512
    assert upload(client, headers).json()["job"]["id"] == value["job"]["id"]
    assert upload(client, headers, content=b"different").status_code == 409
    with TestClient(create_app()) as fresh:
        status = fresh.get("/v1/index-jobs/" + value["job"]["id"], headers=headers)
        assert status.status_code == 200
        assert status.json()["status"] == "pending"
    listing = client.get("/v1/documents", headers=headers)
    assert len(listing.json()["items"]) == 1
    assert listing.headers["cache-control"] == "no-store"
    assert len(list(tmp_path.glob("*.source"))) == 1
    assert "owner_token" not in first.text and str(tmp_path) not in first.text


def test_independent_uploads_and_active_same_content_dedup(client, tmp_path):
    headers, _ = account(client)
    first = upload(client, headers).json()
    other = upload(client, headers, key="another-document").json()
    assert first["document"]["id"] != other["document"]["id"]
    again = upload(client, headers, document=first["document"]["id"], key="same-content")
    assert again.status_code == 200
    assert again.json()["version"]["id"] == first["version"]["id"]
    assert len(list(tmp_path.glob("*.source"))) == 2


def test_owner_isolation_includes_versions_jobs_and_replacement(client):
    a, _ = account(client)
    value = upload(client, a).json()
    b, _ = account(client)
    doc, version, job = value["document"]["id"], value["version"]["id"], value["job"]["id"]
    for path in [
        f"/v1/documents/{doc}",
        f"/v1/documents/{doc}/versions/{version}",
        f"/v1/index-jobs/{job}",
    ]:
        response = client.get(path, headers=b)
        assert response.status_code == 404
        assert response.json() == {"detail": "Document or job not found"}
    assert upload(client, b, document=doc).status_code == 404
    assert client.get("/v1/documents", headers=b).json()["items"] == []
    profiles = client.get("/v1/embedding-profiles", headers=a)
    assert [x["embedding"]["dimension"] for x in profiles.json()] == [512, 1024]


def test_concurrent_identical_submissions_create_one_version_and_file(client, tmp_path):
    headers, _ = account(client)
    barrier = Barrier(2)

    def submit():
        barrier.wait()
        return upload(client, headers)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(r.status_code for r in results) == [200, 202]
    assert len({r.json()["job"]["id"] for r in results}) == 1
    assert len(list(tmp_path.glob("*.source"))) == 1


def test_snapshot_freezes_and_replay_ignores_new_target_profile(client):
    headers, owner = account(client)
    value = upload(client, headers).json()

    async def change(sessions):
        async with sessions() as session, session.begin():
            kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.user_id == owner))
            target = preset("bge-m3")
            kb.build_configuration = target.model_dump(mode="json")
            kb.build_fingerprint = target.fingerprint()

    asyncio.run(database_call(change))
    replay = upload(client, headers).json()
    assert replay["version"]["build_configuration"]["embedding"]["dimension"] == 512
    next_value = upload(client, headers, document=value["document"]["id"], key="m3").json()
    assert next_value["version"]["build_configuration"]["embedding"]["dimension"] == 1024
    assert next_value["version"]["id"] != value["version"]["id"]
    # Superseding a candidate is explicit, never a successful index build.
    old = client.get("/v1/index-jobs/" + value["job"]["id"], headers=headers).json()
    assert old["status"] == "cancelled"


def test_failure_terminal_and_superseded_versions_do_not_resurrect(client):
    headers, _ = account(client)
    first = upload(client, headers).json()

    async def fail(sessions):
        repo = IndexJobRepository(sessions)
        lease = await repo.claim("test-worker", {first["version"]["build_fingerprint"]})
        assert lease is not None
        assert await repo.fail(lease, "BUILD_FAILED")
        assert not await repo.fail(lease, "BUILD_FAILED")
        assert not await repo.progress(lease, "parsing", 1, 1)

    asyncio.run(database_call(fail))
    result = upload(client, headers, document=first["document"]["id"], key="retry").json()
    assert result["version"]["id"] != first["version"]["id"]
    assert result["version"]["version_number"] == 2


def test_new_candidate_does_not_change_published_pointer(client):
    headers, _ = account(client)
    first = upload(client, headers).json()

    async def seed_published_fixture(sessions):
        # Test-only publication fixture, no claim of real vector/manifest validation.
        repo = IndexJobRepository(sessions)
        lease = await repo.claim("fixture", {first["version"]["build_fingerprint"]})
        async with sessions() as session, session.begin():
            document = await session.get(Document, UUID(first["document"]["id"]))
            version = await session.get(DocumentVersion, lease.version_id)
            job = await session.get(IndexJob, lease.job_id)
            version.status = "published"
            job.status, job.finished_at = (
                "succeeded",
                await session.scalar(select(func.clock_timestamp())),
            )
            document.current_version_id, document.pending_version_id = version.id, None

    asyncio.run(database_call(seed_published_fixture))
    next_value = upload(
        client, headers, document=first["document"]["id"], key="v2", content=b"new content"
    ).json()
    assert next_value["document"]["current_version_id"] == first["version"]["id"]
    assert next_value["version"]["version_number"] == 2

    async def fail_candidate(sessions):
        repo = IndexJobRepository(sessions)
        lease = await repo.claim("candidate-worker", {next_value["version"]["build_fingerprint"]})
        assert lease is not None
        assert await repo.fail(lease, "BUILD_FAILED")

    asyncio.run(database_call(fail_candidate))
    document = client.get("/v1/documents/" + first["document"]["id"], headers=headers).json()
    assert document["current_version_id"] == first["version"]["id"]
    assert document["pending_version_id"] is None


def test_claim_race_capability_and_expired_owner_fencing(client):
    headers, _ = account(client)
    first = upload(client, headers).json()

    async def exercise(sessions):
        repo = IndexJobRepository(sessions)
        assert await repo.claim("wrong-profile", {"f" * 64}) is None
        results = await asyncio.gather(
            *[
                repo.claim(
                    "worker-" + str(i), {first["version"]["build_fingerprint"]}, lease_seconds=0.3
                )
                for i in range(2)
            ]
        )
        owners = [x for x in results if x]
        assert len(owners) == 1
        old = owners[0]
        assert not await repo.progress(replace(old, owner_token=uuid4()), "parsing", 1, 2)
        assert await repo.progress(old, "parsing", 1, 2)
        with pytest.raises(ValueError):
            await repo.progress(old, "reserved", 0, None)
        await asyncio.sleep(0.4)
        assert not await repo.heartbeat(old)
        assert not await repo.fail(old, "BUILD_FAILED")
        new = await repo.claim("replacement", {first["version"]["build_fingerprint"]})
        assert new is not None and new.execution_fence == old.execution_fence + 1
        assert new.owner_token != old.owner_token
        assert not await repo.progress(old, "parsing", 2, 2)
        assert await repo.heartbeat(new)
        assert await repo.progress(new, "parsing", 1, 2)
        assert await repo.fail(new, "SOURCE_UNAVAILABLE")

    asyncio.run(database_call(exercise))


def test_database_rejects_version_mutation_and_cross_owner_links(client):
    headers, _ = account(client)
    first = upload(client, headers).json()
    _, other = account(client)

    async def exercise(sessions):
        for statement, params in [
            (
                "UPDATE document_versions SET content_sha256=:hash WHERE id=:id",
                {"hash": "f" * 64, "id": UUID(first["version"]["id"])},
            ),
            (
                "UPDATE documents SET user_id=:owner WHERE id=:id",
                {"owner": other, "id": UUID(first["document"]["id"])},
            ),
            (
                "UPDATE documents SET current_version_id=:version WHERE id=:id",
                {"version": UUID(first["version"]["id"]), "id": UUID(first["document"]["id"])},
            ),
        ]:
            with pytest.raises(IntegrityError):
                async with sessions() as session, session.begin():
                    await session.execute(text(statement), params)

    asyncio.run(database_call(exercise))


def test_filesystem_failure_rolls_back_entire_reservation(client, tmp_path, monkeypatch):
    headers, _ = account(client)

    def broken(*args):
        raise OSError("private filesystem error")

    monkeypatch.setattr(SourceStore, "write", broken)
    response = upload(client, headers)
    assert response.status_code == 503
    assert "private" not in response.text
    assert client.get("/v1/documents", headers=headers).json()["items"] == []
    assert list(tmp_path.iterdir()) == []


def test_commit_failure_retains_source_until_database_confirmed_cleanup(
    client, tmp_path, monkeypatch
):
    headers, owner = account(client)

    async def exercise(sessions):
        source = validate_source("commit.txt", b"source", 100)
        store = SourceStore(tmp_path)
        async with sessions() as session:
            # AsyncSessionTransaction calls its sync transaction, not Session.commit.
            from sqlalchemy import event

            def fail_before_commit(target):
                raise OperationalError("commit", {}, Exception("connection lost"))

            event.listen(session.sync_session, "before_commit", fail_before_commit)
            with pytest.raises(OperationalError):
                await reserve_document(session, store, owner, source, "commit", "bge-small-zh-v1.5")
        assert len(list(tmp_path.glob("*.source"))) == 1
        before = await clean_orphans(sessions, store, minimum_age_seconds=0)
        assert len(before) == 1 and len(list(tmp_path.glob("*.source"))) == 1
        assert await clean_orphans(sessions, store, minimum_age_seconds=0, apply=True) == before
        assert list(tmp_path.glob("*.source")) == []

    asyncio.run(database_call(exercise))


def test_cleanup_waits_for_upload_lock_and_preserves_committed_source(client, tmp_path):
    headers, _ = account(client)
    first = upload(client, headers).json()
    version_id = UUID(first["version"]["id"])

    async def exercise(sessions):
        store = SourceStore(tmp_path)
        async with sessions() as lock_session, lock_session.begin():
            await source_lock(lock_session, version_id)
            task = asyncio.create_task(
                clean_orphans(sessions, store, minimum_age_seconds=0, apply=True)
            )
            await asyncio.sleep(0.05)
            assert not task.done()
        assert await task == []
        assert store.path(version_id).exists()

    asyncio.run(database_call(exercise))


@pytest.mark.parametrize("filename,content", [("a.pdf", b"text"), ("a.txt", b"\xff")])
def test_upload_invalid_text_does_not_reserve_job(client, filename, content):
    headers, _ = account(client)
    response = upload(client, headers, filename=filename, content=content)
    assert response.status_code == 422
    assert client.get("/v1/documents", headers=headers).json()["items"] == []


def test_committed_but_response_failed_reuses_job_and_cleanup_preserves_source(client, tmp_path):
    headers, owner = account(client)

    async def exercise(sessions):
        from sqlalchemy import event

        source = validate_source("commit-ok.txt", b"source", 100)
        store = SourceStore(tmp_path)
        async with sessions() as session:

            def fail_after_commit(target):
                raise OperationalError("commit", {}, Exception("response lost after commit"))

            event.listen(session.sync_session, "after_commit", fail_after_commit)
            with pytest.raises(OperationalError):
                await reserve_document(
                    session, store, owner, source, "commit-ok", "bge-small-zh-v1.5"
                )
        assert await clean_orphans(sessions, store, minimum_age_seconds=0, apply=True) == []
        async with sessions() as session:
            result, created = await reserve_document(
                session, store, owner, source, "commit-ok", "bge-m3"
            )
        assert not created
        assert result.version.build_configuration.embedding.dimension == 512
        assert store.path(result.version.id).exists()
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(IndexJob)) == 1

    asyncio.run(database_call(exercise))


def test_oversize_or_extra_form_fields_do_not_create_document(client):
    headers, _ = account(client)
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"document_max_upload_bytes": 4}
    )
    assert upload(client, headers, content=b"12345").status_code == 413
    response = client.post(
        "/v1/documents",
        headers=headers,
        data={"idempotency_key": "extra", "user_id": str(uuid4())},
        files={"file": ("a.txt", b"ok")},
    )
    assert response.status_code == 422
    assert client.get("/v1/documents", headers=headers).json()["items"] == []


def test_same_bytes_with_different_source_format_build_distinct_versions(client):
    headers, _ = account(client)
    first = upload(client, headers, filename="policy.txt", content=b"# Heading\nbody").json()
    second = upload(
        client,
        headers,
        document=first["document"]["id"],
        key="markdown",
        filename="policy.md",
        content=b"# Heading\nbody",
    ).json()
    assert first["version"]["content_sha256"] == second["version"]["content_sha256"]
    assert first["version"]["build_fingerprint"] != second["version"]["build_fingerprint"]
    assert first["version"]["build_configuration"]["source_format"] == "text"
    assert second["version"]["build_configuration"]["source_format"] == "markdown"
    assert first["version"]["id"] != second["version"]["id"]
