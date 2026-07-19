from io import BytesIO

from pypdf import PdfReader, PdfWriter

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
