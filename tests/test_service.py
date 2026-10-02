from io import BytesIO

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.errors import DependencyError

import app.services.pdf_unlocker as pdf_unlocker
from app.services.pdf_unlocker import unlock_pdf


def _make_locked_pdf(password: str = "secret") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_unlock_pdf_success() -> None:
    locked = _make_locked_pdf("pw123")
    unlocked, error = unlock_pdf(locked, "pw123")

    assert error is None
    assert unlocked is not None

    reader = PdfReader(BytesIO(unlocked))
    assert reader.is_encrypted is False


def test_unlock_pdf_wrong_password() -> None:
    locked = _make_locked_pdf("right")
    unlocked, error = unlock_pdf(locked, "wrong")

    assert unlocked is None
    assert error == "wrong_password_or_unsupported_encryption"


def test_unlock_pdf_invalid_file() -> None:
    unlocked, error = unlock_pdf(b"not-a-pdf", "anything")

    assert unlocked is None
    assert error == "invalid_pdf"


@pytest.mark.parametrize("encryption", [pymupdf.PDF_ENCRYPT_AES_128, pymupdf.PDF_ENCRYPT_AES_256])
def test_unlock_aes_pdf(encryption: int) -> None:
    with pymupdf.open() as document:
        document.new_page().insert_text((40, 80), "ENCRYPTED CONTENT")
        locked = document.tobytes(encryption=encryption, user_pw="pw", owner_pw="owner")

    unlocked, error = unlock_pdf(locked, "pw")
    assert error is None
    assert unlocked is not None
    with pymupdf.open(stream=unlocked, filetype="pdf") as document:
        assert not document.is_encrypted
        assert "ENCRYPTED CONTENT" in document[0].get_text()

    unlocked, error = unlock_pdf(locked, "wrong")
    assert unlocked is None
    assert error == "wrong_password_or_unsupported_encryption"


@pytest.mark.parametrize("error_type", [DependencyError, NotImplementedError])
def test_unsupported_encryption_is_not_reported_as_invalid_pdf(monkeypatch, error_type) -> None:
    def unsupported_reader(*args):
        raise error_type("Unsupported encryption")

    monkeypatch.setattr(pdf_unlocker, "PdfReader", unsupported_reader)
    unlocked, error = unlock_pdf(b"pdf", "pw")
    assert unlocked is None
    assert error == "wrong_password_or_unsupported_encryption"


def test_unlock_preserves_document_structure() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=300, height=200)
    chapter = writer.add_outline_item("Chapter 1", 0)
    writer.add_outline_item("Section 1", 1, parent=chapter)
    writer.add_metadata({"/Title": "Book", "/Author": "Author"})
    writer.add_attachment("notes.txt", b"Notes")
    writer.encrypt("pw")
    stream = BytesIO()
    writer.write(stream)

    unlocked, error = unlock_pdf(stream.getvalue(), "pw")
    assert error is None
    assert unlocked is not None
    reader = PdfReader(BytesIO(unlocked))
    assert not reader.is_encrypted
    assert reader.outline[0].title == "Chapter 1"
    assert reader.get_destination_page_number(reader.outline[0]) == 0
    assert reader.outline[1][0].title == "Section 1"
    assert reader.get_destination_page_number(reader.outline[1][0]) == 1
    assert reader.metadata.title == "Book"
    assert reader.metadata.author == "Author"
    assert reader.attachments["notes.txt"] == [b"Notes"]
