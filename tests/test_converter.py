from io import BytesIO
from pathlib import Path
import subprocess
import zipfile

import pymupdf
import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app.config import settings
from app.services.storage import ArtifactStore
from app.services import word_converter
from app.services.word_converter import WordConversionError, convert_word_to_pdf, find_libreoffice


def make_docx() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
            <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
            <Default Extension="xml" ContentType="application/xml"/>
            <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
            </Types>''')
        archive.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
            </Relationships>''')
        archive.writestr("word/document.xml", '''<?xml version="1.0" encoding="UTF-8"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
            <w:p><w:r><w:rPr><w:b/><w:sz w:val="36"/></w:rPr><w:t>Quarterly Report</w:t></w:r></w:p>
            <w:p><w:r><w:t>Word conversion preserves paragraphs and tables.</w:t></w:r></w:p>
            <w:tbl><w:tblPr><w:tblBorders><w:top w:val="single"/><w:left w:val="single"/><w:bottom w:val="single"/><w:right w:val="single"/><w:insideH w:val="single"/><w:insideV w:val="single"/></w:tblBorders></w:tblPr>
            <w:tblGrid><w:gridCol w:w="3500"/><w:gridCol w:w="3500"/></w:tblGrid>
            <w:tr><w:tc><w:p><w:r><w:t>Category</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Total</w:t></w:r></w:p></w:tc></w:tr>
            <w:tr><w:tc><w:p><w:r><w:t>Revenue</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>1200</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
            <w:p><w:r><w:br w:type="page"/></w:r></w:p>
            <w:p><w:r><w:t>Second page summary</w:t></w:r></w:p>
            <w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>
            </w:body></w:document>''')
    return output.getvalue()


def make_pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page().insert_text((40, 80), "Converted content")
        return document.tobytes()


@pytest.fixture
def conversion_store(tmp_path, monkeypatch):
    monkeypatch.setattr(app_main, "store", ArtifactStore(str(tmp_path), ttl_seconds=300))
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    return tmp_path


def test_conversion_returns_named_download(conversion_store, monkeypatch):
    pdf = make_pdf()
    monkeypatch.setattr(app_main, "convert_word_to_pdf", lambda data, extension: pdf)
    client = TestClient(app_main.app)
    response = client.post("/api/convert/word", files={"file": ("Report.DOCX", make_docx())})
    assert response.status_code == 200
    result = response.json()
    assert result["filename"] == "Report.pdf"
    download = client.get(f"/api/download/{result['request_id']}/{result['download_token']}")
    assert download.status_code == 200
    assert download.content == pdf
    assert "Report.pdf" in download.headers["content-disposition"]


@pytest.mark.parametrize("filename,data", [("wrong.pdf", b"pdf"), ("empty.docx", b""), ("bad.docx", b"bad"), ("bad.doc", b"bad")])
def test_conversion_rejects_invalid_document_before_launch(conversion_store, monkeypatch, filename, data):
    def unexpected_launch(*args):
        raise AssertionError("Converter should not launch")
    monkeypatch.setattr(word_converter, "_run_conversion", unexpected_launch)
    response = TestClient(app_main.app).post("/api/convert/word", files={"file": (filename, data)})
    assert response.status_code in (400, 422)
    assert not list(conversion_store.glob("word-convert-*"))
    assert not list(conversion_store.glob("*/*.pdf"))


def test_conversion_rejects_oversized_file_before_launch(conversion_store, monkeypatch):
    monkeypatch.setattr(settings, "max_file_size_mb", 1)
    def unexpected_launch(*args):
        raise AssertionError("Converter should not launch")
    monkeypatch.setattr(app_main, "convert_word_to_pdf", unexpected_launch)
    response = TestClient(app_main.app).post("/api/convert/word", files={"file": ("large.docx", b"x" * (1024 * 1024 + 1))})
    assert response.status_code == 422
    assert response.json()["detail"] == "File exceeds 1MB"


def test_conversion_reports_missing_engine(conversion_store, monkeypatch):
    monkeypatch.setattr(settings, "libreoffice_path", str(conversion_store / "missing.exe"))
    response = TestClient(app_main.app).post("/api/convert/word", files={"file": ("sample.docx", make_docx())})
    assert response.status_code == 503
    assert "install LibreOffice" in response.json()["detail"]


@pytest.mark.parametrize("outcome", ["timeout", "failed", "invalid_pdf"])
def test_conversion_cleans_temporary_source_after_failure(conversion_store, monkeypatch, outcome):
    monkeypatch.setattr(word_converter, "find_libreoffice", lambda: "soffice")
    def convert(command):
        source = Path(command[-1])
        assert source.exists()
        if outcome == "timeout":
            raise WordConversionError("Conversion timed out", 504)
        if outcome == "invalid_pdf":
            (source.parent / "output" / "document.pdf").write_bytes(b"not a PDF")
            return 0
        return 1
    monkeypatch.setattr(word_converter, "_run_conversion", convert)
    response = TestClient(app_main.app).post("/api/convert/word", files={"file": ("sample.docx", make_docx())})
    assert response.status_code == (504 if outcome == "timeout" else 400)
    assert not list(conversion_store.glob("word-convert-*"))
    assert not list(conversion_store.glob("*/*.pdf"))


def test_docx_expansion_limit(conversion_store, monkeypatch):
    monkeypatch.setattr(settings, "max_total_size_mb", 1)
    source = BytesIO(make_docx())
    with zipfile.ZipFile(source, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/media/large.bin", b"x" * (1024 * 1024))
    with pytest.raises(WordConversionError, match="expands beyond"):
        convert_word_to_pdf(source.getvalue(), ".docx")


def test_timeout_stops_converter_process(monkeypatch):
    class Process:
        pid = 12345
        calls = 0
        def wait(self, timeout=None):
            self.calls += 1
            if timeout is not None:
                raise subprocess.TimeoutExpired("soffice", timeout)
            return -1
    process = Process()
    monkeypatch.setattr(word_converter.subprocess, "Popen", lambda *args, **kwargs: process)
    killed = []
    monkeypatch.setattr(word_converter.subprocess, "run", lambda command, **kwargs: killed.append(command))
    monkeypatch.setattr(word_converter.os, "killpg", lambda *args: killed.append(args), raising=False)
    with pytest.raises(WordConversionError, match="timed out") as error:
        word_converter._run_conversion(["soffice"])
    assert error.value.status_code == 504
    assert process.calls == 2
    assert killed


@pytest.mark.parametrize("extension", [".docx", ".doc"])
def test_real_word_conversion(conversion_store, extension):
    try:
        executable = find_libreoffice()
    except WordConversionError:
        pytest.skip("LibreOffice is not installed")
    source = make_docx()
    if extension == ".doc":
        fixture = conversion_store / "fixture.docx"
        fixture.write_bytes(source)
        profile = (conversion_store / "fixture-profile").resolve().as_uri()
        result = subprocess.run([executable, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "doc:MS Word 97", "--outdir", str(conversion_store), str(fixture)],
                                capture_output=True, timeout=90)
        assert result.returncode == 0
        source = (conversion_store / "fixture.doc").read_bytes()
    result = convert_word_to_pdf(source, extension)
    with pymupdf.open(stream=result, filetype="pdf") as document:
        assert document.page_count == 2
        assert "Quarterly Report" in document[0].get_text()
        assert "Revenue" in document[0].get_text()
        assert "1200" in document[0].get_text()
        assert "Second page summary" in document[1].get_text()
    assert not list(conversion_store.glob("word-convert-*"))
