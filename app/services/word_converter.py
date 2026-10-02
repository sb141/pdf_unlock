"""Convert Word documents with a private, short-lived LibreOffice profile."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

import pymupdf

from app.config import settings


class WordConversionError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def find_libreoffice() -> str:
    if settings.libreoffice_path:
        executable = Path(settings.libreoffice_path).expanduser()
        if executable.is_file():
            return str(executable.resolve())
    else:
        for name in ("soffice", "libreoffice"):
            if executable := shutil.which(name):
                return executable
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            if directory := os.environ.get(variable):
                for name in ("soffice.com", "soffice.exe"):
                    executable = Path(directory) / "LibreOffice" / "program" / name
                    if executable.is_file():
                        return str(executable)
        executable = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        if executable.is_file():
            return str(executable)
    raise WordConversionError("Word conversion is unavailable. Ask the server administrator to install LibreOffice.", 503)


def _validate_document(data: bytes, extension: str) -> None:
    if extension == ".doc":
        if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            return
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = set(archive.namelist())
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                    raise WordConversionError("Invalid Word document")
                if len(archive.infolist()) > 10000 or sum(part.file_size for part in archive.infolist()) > settings.max_total_size_mb * 1024 * 1024:
                    raise WordConversionError("Word document expands beyond the conversion limit", 422)
                if any(part.flag_bits & 1 for part in archive.infolist()):
                    raise WordConversionError("Remove the document password before converting")
                return
        except (zipfile.BadZipFile, OSError) as exc:
            raise WordConversionError("Invalid Word document") from exc
    raise WordConversionError("Invalid Word document")


def _run_conversion(command: list[str]) -> int:
    options = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
    except OSError as exc:
        raise WordConversionError("The Word converter could not start. Contact the server administrator.", 503) from exc
    try:
        return process.wait(timeout=settings.conversion_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        if os.name == "nt":
            # Stop the launcher and soffice.bin so a timed-out job cannot keep running.
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        raise WordConversionError("Conversion timed out. Try a smaller or simpler document.", 504) from exc


def convert_word_to_pdf(data: bytes, extension: str) -> bytes:
    extension = extension.lower()
    _validate_document(data, extension)
    executable = find_libreoffice()
    Path(settings.temp_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="word-convert-", dir=settings.temp_dir) as directory:
        workspace = Path(directory).resolve()
        source = workspace / f"document{extension}"
        source.write_bytes(data)
        output = workspace / "output"
        output.mkdir()
        profile = workspace / "profile"
        (profile / "user").mkdir(parents=True)
        # Never execute document macros or update linked content during conversion.
        (profile / "user" / "registrymodifications.xcu").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>'
            '<item oor:path="/org.openoffice.Office.Writer/Content/Update">'
            '<prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>'
            '</oor:items>', encoding="utf-8",
        )
        document_filter = "MS Word 2007 XML" if extension == ".docx" else "MS Word 97"
        return_code = _run_conversion([
            executable, f"-env:UserInstallation={profile.as_uri()}",
            "--headless", "--nologo", "--nodefault", "--norestore",
            f"--infilter={document_filter}", "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", str(output), str(source),
        ])
        pdf = output / "document.pdf"
        if return_code != 0 or not pdf.is_file():
            raise WordConversionError("Could not convert this Word document. Check that it opens in Word and has no password.")
        if pdf.stat().st_size > settings.max_total_size_mb * 1024 * 1024:
            raise WordConversionError("Converted PDF exceeds the output size limit", 422)
        result = pdf.read_bytes()
        try:
            with pymupdf.open(stream=result, filetype="pdf") as document:
                if document.needs_pass or document.page_count == 0:
                    raise ValueError("Empty or encrypted output")
        except Exception as exc:
            raise WordConversionError("The converter did not produce a valid PDF") from exc
        return result
