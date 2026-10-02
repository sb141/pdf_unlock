import asyncio
from tempfile import SpooledTemporaryFile

import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app.config import settings
from app.upload_limits import UploadLimitMiddleware


@pytest.mark.parametrize("path", ["/api/unlock", "/api/edit/session", "/api/convert/word"])
def test_declared_oversized_upload_is_rejected_before_spooling(path, monkeypatch):
    monkeypatch.setattr(settings, "max_total_size_mb", 1)
    monkeypatch.setattr(settings, "max_file_size_mb", 1)
    def unexpected_spool(*args, **kwargs):
        raise AssertionError("multipart buffering started")
    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", unexpected_spool)
    response = TestClient(app_main.app).post(path, files={
        "files" if path == "/api/unlock" else "file": ("large.pdf", b"x" * (3 * 1024 * 1024)),
    }, data={"password": "pw"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Upload request is too large"


@pytest.mark.parametrize("path", ["/api/unlock", "/api/edit/session", "/api/convert/word"])
def test_chunked_oversized_upload_stops_reading_and_closes_spool(path, monkeypatch):
    monkeypatch.setattr(settings, "max_total_size_mb", 1)
    monkeypatch.setattr(settings, "max_file_size_mb", 1)
    spools = []
    def tracked_spool(*args, **kwargs):
        spool = SpooledTemporaryFile(*args, **kwargs)
        spools.append(spool)
        return spool
    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", tracked_spool)
    field = "files" if path == "/api/unlock" else "file"
    header = (f'--boundary\r\nContent-Disposition: form-data; name="{field}"; '
              'filename="large.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()
    chunks = [header, b"x" * (1024 * 1024), b"x" * (1024 * 1024), b"never read"]
    calls = 0
    messages = []
    async def receive():
        nonlocal calls
        chunk = chunks[calls]
        calls += 1
        return {"type": "http.request", "body": chunk, "more_body": True}
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "server": ("test", 80), "client": ("test", 1),
             "headers": [(b"content-type", b"multipart/form-data; boundary=boundary")]}
    asyncio.run(app_main.app(scope, receive, send))
    assert calls == 3
    assert messages[0]["status"] == 422
    assert spools and all(spool.closed for spool in spools)


def test_other_endpoints_are_not_subject_to_upload_limit():
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    messages = []
    async def receive():
        raise AssertionError("body should not be read")
    async def send(message):
        messages.append(message)
    asyncio.run(UploadLimitMiddleware(inner)({"type": "http", "method": "GET", "path": "/healthz"}, receive, send))
    assert messages[0]["status"] == 200
