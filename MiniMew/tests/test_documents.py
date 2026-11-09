import hashlib
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from minimew.app import create_app
from minimew.database import get_session
from minimew.documents.profiles import BuildConfiguration, preset, read_build
from minimew.documents.storage import PROJECT_ROOT, InvalidSource, SourceStore, validate_source


def test_presets_are_pinned_distinct_and_frozen():
    small, m3 = preset("bge-small-zh-v1.5"), preset("bge-m3")
    assert (small.embedding.dimension, m3.embedding.dimension) == (512, 1024)
    assert small.fingerprint() != m3.fingerprint()
    assert len(small.embedding.revision) == len(m3.embedding.revision) == 40
    assert read_build(small.model_dump(mode="json"), small.fingerprint()) == small
    with pytest.raises(ValidationError):
        small.embedding.dimension = 1024
    with pytest.raises(ValueError):
        preset("arbitrary/model")


@pytest.mark.parametrize(
    "change",
    [
        {"api_key": "never-save-secrets"},
        {"chunk_tokens": 9999},
        {"overlap_tokens": 384},
        {"schema_version": 2},
    ],
)
def test_build_rejects_extra_fields_and_invalid_budgets(change):
    value = {**preset("bge-small-zh-v1.5").model_dump(mode="json"), **change}
    with pytest.raises(ValidationError):
        BuildConfiguration.model_validate(value)


def test_fingerprint_covers_revision_and_encoding_policy():
    build = preset("bge-small-zh-v1.5")
    value = build.model_dump(mode="json")
    value["embedding"]["revision"] = "0" * 40
    changed = BuildConfiguration.model_validate(value)
    assert changed.fingerprint() != build.fingerprint()
    with pytest.raises(ValueError):
        read_build(value, build.fingerprint())
    value["embedding"]["dimension"] = 1024
    with pytest.raises(ValidationError):
        BuildConfiguration.model_validate(value)


@pytest.mark.parametrize(
    "filename,content,limit",
    [
        ("../escape.md", b"text", 10),
        ("C:\\file.txt", b"text", 10),
        ("a.pdf", b"text", 10),
        ("a.txt", b"", 10),
        ("a.txt", b"   ", 10),
        ("a.txt", b"\xff", 10),
        ("a.txt", b"a\0b", 10),
        ("a.txt", b"12345", 4),
    ],
)
def test_invalid_source_is_rejected(filename, content, limit):
    with pytest.raises(InvalidSource):
        validate_source(filename, content, limit)


def test_sources_are_immutable_and_integrity_checked(tmp_path):
    store = SourceStore(tmp_path)
    content = "\ufeff# 出差\n餐费 150 元。".encode()
    source = validate_source("中文.MD", content, 100)
    version = uuid4()
    store.write(version, source)
    assert source.format == "markdown"
    assert source.sha256 == hashlib.sha256(content).hexdigest()
    assert store.read(version, source.sha256) == content
    assert store.path(version).name == f"{version}.source"
    with pytest.raises(FileExistsError):
        store.write(version, source)
    with pytest.raises(OSError):
        store.read(version, "0" * 64)
    assert not list(tmp_path.glob(".upload-*"))


def test_storage_failure_does_not_leave_partial_files(tmp_path, monkeypatch):
    store = SourceStore(tmp_path)

    def broken(*args):
        raise OSError("test failure")

    monkeypatch.setattr("minimew.documents.storage.os.link", broken)
    with pytest.raises(OSError):
        store.write(uuid4(), validate_source("a.txt", b"hello", 100))
    assert list(tmp_path.iterdir()) == []


def test_relative_storage_is_anchored_to_project_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert SourceStore(Path("data/documents")).root == PROJECT_ROOT / "data" / "documents"


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/documents"),
        ("GET", "/v1/documents"),
        ("GET", f"/v1/documents/{uuid4()}"),
        ("POST", f"/v1/documents/{uuid4()}/versions"),
        ("GET", f"/v1/documents/{uuid4()}/versions/{uuid4()}"),
        ("GET", f"/v1/index-jobs/{uuid4()}"),
        ("GET", "/v1/embedding-profiles"),
    ],
)
@pytest.mark.parametrize("token", [None, "invalid-token"])
def test_document_routes_reject_bad_identity_before_database(monkeypatch, method, path, token):
    monkeypatch.setenv("MINIMEW_DB_PASSWORD", "offline-only")
    session = MagicMock()

    async def offline_session():
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = offline_session
    headers = {"Authorization": "Bearer " + token} if token else {}
    with TestClient(app) as client:
        response = client.request(
            method,
            path,
            headers=headers,
            data={"idempotency_key": "key"} if method == "POST" else None,
            files={"file": ("a.txt", b"hello")} if method == "POST" else None,
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert session.mock_calls == []


def test_total_upload_budget_checks_stream_without_content_length(monkeypatch):
    import asyncio

    from fastapi import FastAPI, Request

    from minimew.api.documents import UploadLimitMiddleware

    app = FastAPI()
    app.add_middleware(UploadLimitMiddleware, max_bytes=10)

    @app.post("/v1/documents")
    async def consume(request: Request):
        return {"size": len(await request.body())}

    async def invoke():
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"a" * 65547, "more_body": False}

        async def send(value):
            sent.append(value)

        await app(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/documents",
                "query_string": b"",
                "headers": [],
                "scheme": "http",
                "server": ("localhost", 80),
            },
            receive,
            send,
        )
        assert sent[0]["status"] == 413

    asyncio.run(invoke())
