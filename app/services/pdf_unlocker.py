import logging
from io import BytesIO

from pypdf import PdfReader, PdfWriter

logger = logging.getLogger("pdf_unlock")


def unlock_pdf(input_bytes: bytes, password: str) -> tuple[bytes | None, str | None]:
    """Return unlocked bytes or an error code."""
    try:
        reader = PdfReader(BytesIO(input_bytes))
    except Exception as e:
        logger.exception("Failed to parse PDF bytes")
        return None, "invalid_pdf"

    if not reader.is_encrypted:
        logger.warning("PDF is not encrypted / already unlocked")
        return None, "decrypt_failed"

    try:
        result = reader.decrypt(password)
    except Exception as e:
        logger.exception("Exception during reader.decrypt")
        return None, "wrong_password_or_unsupported_encryption"

    if result == 0:
        logger.warning("Decryption failed: wrong password or unsupported encryption")
        return None, "wrong_password_or_unsupported_encryption"

    writer = PdfWriter()
    try:
        for page in reader.pages:
            writer.add_page(page)
    except Exception as e:
        logger.exception("Exception adding pages to writer")
        return None, "decrypt_failed"

    output = BytesIO()
    try:
        writer.write(output)
    except Exception as e:
        logger.exception("Exception writing unlocked PDF bytes")
        return None, "decrypt_failed"

    return output.getvalue(), None
