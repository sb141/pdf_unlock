from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter

import app.main as app_main
from app.services.storage import ArtifactStore


client = TestClient(app_main.app)


def _make_locked_pdf(password: str = "secret") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _install_temp_store(tmp_path: Path) -> None:
    app_main.store = ArtifactStore(base_dir=str(tmp_path), ttl_seconds=300)


def test_unlock_single_file_success(tmp_path: Path) -> None:
    _install_temp_store(tmp_path)

    files = [("files", ("locked.pdf", _make_locked_pdf("pw123"), "application/pdf"))]
    response = client.post("/api/unlock", files=files, data={"password": "pw123"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["succeeded"] == 1

    token = payload["results"][0]["download_token"]
    request_id = payload["request_id"]

    download = client.get(f"/api/download/{request_id}/{token}")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")


def test_unlock_batch_partial_success_and_zip(tmp_path: Path) -> None:
    _install_temp_store(tmp_path)

    files = [
        ("files", ("ok1.pdf", _make_locked_pdf("pw"), "application/pdf")),
        ("files", ("ok2.pdf", _make_locked_pdf("pw"), "application/pdf")),
        ("files", ("bad.pdf", _make_locked_pdf("different"), "application/pdf")),
    ]
    response = client.post("/api/unlock", files=files, data={"password": "pw"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["succeeded"] == 2
    assert payload["summary"]["failed"] == 1
    assert payload["batch_download_available"] is True

    request_id = payload["request_id"]
    batch_download = client.get(f"/api/download-batch/{request_id}")
    assert batch_download.status_code == 200
    assert batch_download.headers["content-type"].startswith("application/zip")


def test_reject_non_pdf_extension(tmp_path: Path) -> None:
    _install_temp_store(tmp_path)

    files = [("files", ("not_pdf.txt", b"hello", "text/plain"))]
    response = client.post("/api/unlock", files=files, data={"password": "pw"})

    assert response.status_code == 400
    assert response.json()["detail"] == "All files failed to unlock"
