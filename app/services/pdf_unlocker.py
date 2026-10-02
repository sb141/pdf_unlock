import logging
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.errors import DependencyError

logger = logging.getLogger("pdf_unlock")


def unlock_pdf(input_bytes: bytes, password: str) -> tuple[bytes | None, str | None]:
    """Return unlocked bytes or an error code."""
    try:
        reader = PdfReader(BytesIO(input_bytes))
    except (DependencyError, NotImplementedError):
        logger.exception("Unsupported PDF encryption")
        return None, "wrong_password_or_unsupported_encryption"
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
        writer.clone_document_from_reader(reader)
    except Exception as e:
        logger.exception("Exception cloning document to writer")
        return None, "decrypt_failed"

    output = BytesIO()
    try:
        writer.write(output)
    except Exception as e:
        logger.exception("Exception writing unlocked PDF bytes")
        return None, "decrypt_failed"

    return output.getvalue(), None
