from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app.services.storage import ArtifactStore
from app.models import AddedText, Redaction, TextReplacement
from app.services.pdf_editor import apply_edits, get_text_spans


client = TestClient(app_main.app)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("scales", [(1, 1), (2, 0.5)])
def test_added_text_matches_visible_page_orientation(rotation: int, scales: tuple[float, float]) -> None:
    with pymupdf.open() as document:
        document.new_page(width=400, height=400).set_rotation(rotation)
        source = document.tobytes()
    scale_x, scale_y = scales
    edited = apply_edits(source, [], [AddedText(
        page=0, x=100, y=150, text="ADDED", font_size=18, scale_x=scale_x, scale_y=scale_y,
    )])
    with pymupdf.open(stream=edited, filetype="pdf") as document:
        page = document[0]
        assert page.rotation == rotation
        line = next(line for block in page.get_text("dict")["blocks"] if block.get("type") == 0
                    for line in block["lines"])
        direction = pymupdf.Point(line["dir"]) * pymupdf.Matrix(rotation)
        assert (direction.x, direction.y) == pytest.approx((1, 0), abs=0.001)
        span = line["spans"][0]
        assert span["text"] == "ADDED"
        origin = pymupdf.Point(span["origin"]) * page.rotation_matrix
        assert (origin.x, origin.y) == pytest.approx((100, 150), abs=0.001)
        bounds = pymupdf.Rect(span["bbox"]) * page.rotation_matrix
        assert bounds.width == pytest.approx(pymupdf.get_text_length("ADDED", fontsize=18) * scale_x, abs=0.01)
        assert bounds.height == pytest.approx(18 * (1.075 + 0.299) * scale_y, abs=0.01)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_replacement_text_matches_visible_page_orientation(rotation: int) -> None:
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=400)
        page.set_rotation(rotation)
        point = pymupdf.Point(100, 150) * page.derotation_matrix
        page.insert_text(point, "ORIGINAL", fontsize=18, rotate=rotation)
        source = document.tobytes()
    edited = apply_edits(source, [], [], [TextReplacement(page=0, span_id=0, text="UPDATED")])
    with pymupdf.open(stream=edited, filetype="pdf") as document:
        page = document[0]
        line = next(line for block in page.get_text("dict")["blocks"] if block.get("type") == 0
                    for line in block["lines"])
        direction = pymupdf.Point(line["dir"]) * pymupdf.Matrix(rotation)
        assert (direction.x, direction.y) == pytest.approx((1, 0), abs=0.001)
        assert line["spans"][0]["text"] == "UPDATED"
        origin = pymupdf.Point(line["spans"][0]["origin"]) * page.rotation_matrix
        assert (origin.x, origin.y) == pytest.approx((100, 150), abs=0.001)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_redaction_takes_precedence_over_replacements_and_added_text(rotation: int) -> None:
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=400)
        page.set_rotation(rotation)
        page.insert_text(pymupdf.Point(40, 80) * page.derotation_matrix, "SECRET", fontsize=18, rotate=rotation)
        page.insert_text(pymupdf.Point(40, 180) * page.derotation_matrix, "PUBLIC", fontsize=18, rotate=rotation)
        source = document.tobytes()
    secret = next(span for span in get_text_spans(source, 0) if span["text"] == "SECRET")
    edited = apply_edits(source, [Redaction(page=0, x0=35, y0=50, x1=170, y1=110)],
                         [AddedText(page=0, x=40, y=100, text="ADDED", font_size=18)],
                         [TextReplacement(page=0, span_id=secret["span_id"], text="SECRET")])
    with pymupdf.open(stream=edited, filetype="pdf") as document:
        text = document[0].get_text()
        assert "SECRET" not in text
        assert "ADDED" not in text
        assert "PUBLIC" in text
        pixmap = document[0].get_pixmap()
        assert pixmap.pixel(60, 80) == (0, 0, 0)


def _pdf_with_secret() -> tuple[bytes, list[float]]:
    document = pymupdf.open()
    page = document.new_page(width=400, height=300)
    page.insert_text((40, 80), "SECRET", fontsize=18)
    page.insert_text((40, 130), "PUBLIC", fontsize=18)
    rectangle = page.search_for("SECRET")[0]
    data = document.tobytes()
    document.close()
    return data, [rectangle.x0, rectangle.y0, rectangle.x1, rectangle.y1]


def test_editor_removes_redacted_text_and_adds_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "store", ArtifactStore(str(tmp_path), ttl_seconds=300))
    source, rectangle = _pdf_with_secret()
    opened = client.post(
        "/api/edit/session",
        files={"file": ("sample.pdf", source, "application/pdf")},
    )
    assert opened.status_code == 200
    session = opened.json()
    assert session["pages"] == [{"width": 400.0, "height": 300.0}]

    prefix = f"/api/edit/{session['request_id']}/{session['source_token']}"
    preview = client.get(f"{prefix}/pages/0")
    assert preview.status_code == 200
    assert preview.content.startswith(b"\x89PNG\r\n\x1a\n")

    edited = client.post(prefix, json={
        "redactions": [{"page": 0, "x0": rectangle[0], "y0": rectangle[1], "x1": rectangle[2], "y1": rectangle[3]}],
        "texts": [{"page": 0, "x": 40, "y": 180, "text": "REVIEWED", "font_size": 14,
                   "font_name": "hebo", "color": "#0000cc"}],
    })
    assert edited.status_code == 200
    result = edited.json()
    download = client.get(f"/api/download/{result['request_id']}/{result['download_token']}")
    assert download.status_code == 200

    with pymupdf.open(stream=download.content, filetype="pdf") as document:
        text = document[0].get_text()
        assert "SECRET" not in text
        assert "PUBLIC" in text
        assert "REVIEWED" in text
        reviewed = next(span for block in document[0].get_text("dict")["blocks"] if block.get("type") == 0
                        for line in block["lines"] for span in line["spans"] if "REVIEWED" in span["text"])
        assert reviewed["color"] == 0x0000cc
        redaction_center = pymupdf.Point((rectangle[0] + rectangle[2]) / 2, (rectangle[1] + rectangle[3]) / 2)
        pixmap = document[0].get_pixmap()
        pixel = pixmap.pixel(int(redaction_center.x), int(redaction_center.y))
        assert pixel == (0, 0, 0)


def test_editor_rejects_rectangle_outside_page(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "store", ArtifactStore(str(tmp_path), ttl_seconds=300))
    source, _ = _pdf_with_secret()
    session = client.post("/api/edit/session", files={"file": ("sample.pdf", source, "application/pdf")}).json()
    prefix = f"/api/edit/{session['request_id']}/{session['source_token']}"
    response = client.post(prefix, json={
        "redactions": [{"page": 0, "x0": 390, "y0": 10, "x1": 410, "y1": 50}],
    })
    assert response.status_code == 422


def test_editor_requires_password_for_locked_pdf(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "store", ArtifactStore(str(tmp_path), ttl_seconds=300))
    source, _ = _pdf_with_secret()
    with pymupdf.open(stream=source, filetype="pdf") as document:
        locked = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    files = {"file": ("locked.pdf", locked, "application/pdf")}
    wrong = client.post("/api/edit/session", files=files, data={"password": "wrong"})
    assert wrong.status_code == 400
    good = client.post("/api/edit/session", files=files, data={"password": "pw"})
    assert good.status_code == 200


def test_redaction_coordinates_follow_rotated_page(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "store", ArtifactStore(str(tmp_path), ttl_seconds=300))
    document = pymupdf.open()
    page = document.new_page(width=300, height=400)
    page.insert_text((40, 80), "ROTATED SECRET", fontsize=18)
    page.set_rotation(90)
    visible_rect = page.search_for("ROTATED SECRET")[0] * page.rotation_matrix
    source = document.tobytes()
    document.close()

    session = client.post("/api/edit/session", files={"file": ("rotated.pdf", source, "application/pdf")}).json()
    assert session["pages"] == [{"width": 400.0, "height": 300.0}]
    prefix = f"/api/edit/{session['request_id']}/{session['source_token']}"
    result = client.post(prefix, json={"redactions": [{
        "page": 0, "x0": visible_rect.x0, "y0": visible_rect.y0,
        "x1": visible_rect.x1, "y1": visible_rect.y1,
    }]})
    assert result.status_code == 200
    token = result.json()["download_token"]
    saved = client.get(f"/api/download/{session['request_id']}/{token}")
    with pymupdf.open(stream=saved.content, filetype="pdf") as edited:
        assert "ROTATED SECRET" not in edited[0].get_text()


def test_editor_rejects_invalid_pdf(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "store", ArtifactStore(str(tmp_path), ttl_seconds=300))
    response = client.post(
        "/api/edit/session",
        files={"file": ("broken.pdf", b"not a PDF", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid PDF file"


def test_editor_replaces_selectable_text_with_requested_style(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "store", ArtifactStore(str(tmp_path), ttl_seconds=300))
    source, _ = _pdf_with_secret()
    session = client.post("/api/edit/session", files={"file": ("sample.pdf", source, "application/pdf")}).json()
    prefix = f"/api/edit/{session['request_id']}/{session['source_token']}"

    spans_response = client.get(f"{prefix}/pages/0/text")
    assert spans_response.status_code == 200
    secret = next(span for span in spans_response.json() if span["text"] == "SECRET")
    assert secret["x0"] < secret["x1"]

    saved = client.post(prefix, json={"replacements": [{
        "page": 0, "span_id": secret["span_id"], "text": "UPDATED",
        "font_size": 18, "font_name": "hebo", "color": "#cc0000",
    }]})
    assert saved.status_code == 200
    token = saved.json()["download_token"]
    download = client.get(f"/api/download/{session['request_id']}/{token}")
    with pymupdf.open(stream=download.content, filetype="pdf") as document:
        assert "SECRET" not in document[0].get_text()
        assert "UPDATED" in document[0].get_text()
        assert "PUBLIC" in document[0].get_text()
        updated = next(span for block in document[0].get_text("dict")["blocks"] if block.get("type") == 0
                       for line in block["lines"] for span in line["spans"] if "UPDATED" in span["text"])
        assert updated["color"] == 0xcc0000


def test_editor_rejects_unknown_text_span(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "store", ArtifactStore(str(tmp_path), ttl_seconds=300))
    source, _ = _pdf_with_secret()
    session = client.post("/api/edit/session", files={"file": ("sample.pdf", source, "application/pdf")}).json()
    prefix = f"/api/edit/{session['request_id']}/{session['source_token']}"
    response = client.post(prefix, json={"replacements": [{"page": 0, "span_id": 999, "text": "X"}]})
    assert response.status_code == 422


def test_editor_deletes_selected_pdf_text_without_replacement(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "store", ArtifactStore(str(tmp_path), ttl_seconds=300))
    source, _ = _pdf_with_secret()
    session = client.post("/api/edit/session", files={"file": ("sample.pdf", source, "application/pdf")}).json()
    prefix = f"/api/edit/{session['request_id']}/{session['source_token']}"
    secret = next(span for span in client.get(f"{prefix}/pages/0/text").json() if span["text"] == "SECRET")

    saved = client.post(prefix, json={"replacements": [{
        "page": 0, "span_id": secret["span_id"], "text": "",
    }]})
    assert saved.status_code == 200
    token = saved.json()["download_token"]
    download = client.get(f"/api/download/{session['request_id']}/{token}")
    with pymupdf.open(stream=download.content, filetype="pdf") as document:
        assert "SECRET" not in document[0].get_text()
        assert "PUBLIC" in document[0].get_text()
