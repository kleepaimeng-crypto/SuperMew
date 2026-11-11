"""最近Job由Catalog派生，刷新失败与幂等旧回执都保留真实状态。"""

import asyncio
import os
from uuid import UUID

import pytest
from test_documents_pg import account, database_call, upload
from test_documents_pg import client as document_client

from minimew.documents.jobs import IndexJobRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("MINIMEW_INTEGRATION_DB"),
    reason="Explicit owned temporary PostgreSQL required",
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    yield from document_client.__wrapped__(monkeypatch, tmp_path)


def test_failed_job_survives_pending_pointer_clear_in_list_and_detail(client):
    headers, _ = account(client)
    value = upload(client, headers).json()

    async def fail(sessions):
        repo = IndexJobRepository(sessions)
        lease = await repo.claim("projection-test", {value["version"]["build_fingerprint"]})
        assert lease.version_id == UUID(value["version"]["id"])
        assert await repo.fail(lease, "BUILD_FAILED")

    asyncio.run(database_call(fail))
    detail = client.get("/v1/documents/" + value["document"]["id"], headers=headers)
    listing = client.get("/v1/documents", headers=headers)
    for document in (detail.json(), listing.json()["items"][0]):
        assert document["pending_version_id"] is None
        assert document["latest_job"]["id"] == value["job"]["id"]
        assert document["latest_job"]["status"] == "failed"
        assert document["latest_job"]["error_code"] == "BUILD_FAILED"
    assert detail.json() == listing.json()["items"][0]
    assert detail.headers["cache-control"] == "no-store"
    assert all(
        secret not in detail.text
        for secret in ("owner_token", "lease_until", "worker_id", "last_error_code")
    )


def test_old_idempotent_receipt_returns_original_job_but_current_document_activity(client):
    headers, _ = account(client)
    one = upload(client, headers).json()
    two = upload(client, headers, document=one["document"]["id"], key="new", content=b"new").json()
    replay = upload(client, headers).json()
    assert replay["job"]["id"] == one["job"]["id"]
    assert replay["job"]["status"] == "cancelled"
    assert replay["version"]["id"] == one["version"]["id"]
    assert replay["document"]["latest_job"]["id"] == two["job"]["id"]
    assert replay["document"]["pending_version_id"] == two["version"]["id"]
    assert two["document"]["latest_job"] == two["job"]


def test_latest_job_projection_remains_user_scoped_and_paginated(client):
    a, _ = account(client)
    first = upload(client, a).json()
    second = upload(client, a, key="second-document").json()
    b, _ = account(client)
    upload(client, b, key="other-account")
    page = client.get("/v1/documents?limit=1", headers=a).json()
    rest = client.get("/v1/documents?limit=1&offset=1", headers=a).json()
    assert page["next_offset"] == 1 and rest["next_offset"] is None
    assert {page["items"][0]["latest_job"]["id"], rest["items"][0]["latest_job"]["id"]} == {
        first["job"]["id"],
        second["job"]["id"],
    }
    assert client.get("/v1/documents/" + first["document"]["id"], headers=b).status_code == 404
