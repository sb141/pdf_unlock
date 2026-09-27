from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

import app.main as app_main
from app.services.storage import ArtifactStore


client = TestClient(app_main.app)


def _make_locked_pdf(password: str = "secret", width: int = 200) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=200)
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


def test_duplicate_names_keep_distinct_downloads_and_zip_entries(tmp_path: Path) -> None:
    _install_temp_store(tmp_path)
    files = [
        ("files", ("same.pdf", _make_locked_pdf("pw", width=200), "application/pdf")),
        ("files", ("same.pdf", _make_locked_pdf("pw", width=300), "application/pdf")),
    ]
    response = client.post("/api/unlock", files=files, data={"password": "pw"})

    assert response.status_code == 200
    payload = response.json()
    request_id = payload["request_id"]
    widths = []
    for result in payload["results"]:
        download = client.get(f"/api/download/{request_id}/{result['download_token']}")
        assert download.status_code == 200
        widths.append(float(PdfReader(BytesIO(download.content)).pages[0].mediabox.width))
    assert widths == [200.0, 300.0]

    batch = client.get(f"/api/download-batch/{request_id}")
    with ZipFile(BytesIO(batch.content)) as archive:
        pdf_names = [name for name in archive.namelist() if name.endswith(".pdf")]
        assert pdf_names == ["same_unlocked.pdf", "same_unlocked_2.pdf"]


def test_total_limit_rejects_before_any_conversion_or_artifact(tmp_path: Path, monkeypatch) -> None:
    _install_temp_store(tmp_path)
    monkeypatch.setattr(app_main.settings, "max_total_size_mb", 1)

    def should_not_unlock(*args):
        raise AssertionError("conversion started before request size validation")

    monkeypatch.setattr(app_main, "unlock_pdf", should_not_unlock)
    files = [
        ("files", ("first.pdf", _make_locked_pdf("pw"), "application/pdf")),
        ("files", ("second.pdf", b"x" * (1024 * 1024), "application/pdf")),
    ]
    response = client.post("/api/unlock", files=files, data={"password": "pw"})

    assert response.status_code == 422
    assert list(tmp_path.iterdir()) == []
