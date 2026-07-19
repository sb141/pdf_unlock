from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.models import FileResult, FileStatus, Summary, UnlockResponse
from app.services.pdf_unlocker import unlock_pdf
from app.services.storage import ArtifactStore


logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger("pdf_unlock")

app = FastAPI(title="PDF Unlock API", version="1.0.0")
store = ArtifactStore(base_dir=settings.temp_dir, ttl_seconds=settings.download_ttl_seconds)


app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    store.purge_now()
    return {"status": "ok"}


def _validate_pdf_name(name: str) -> bool:
    return name.lower().endswith(".pdf")


def _unlocked_name(original_name: str) -> str:
    path = Path(original_name)
    return f"{path.stem}_unlocked.pdf"


@app.post("/api/unlock", response_model=UnlockResponse)
async def unlock_endpoint(
    password: Annotated[str, Form(min_length=1)],
    files: Annotated[list[UploadFile], File()],
) -> UnlockResponse:
    request_id = store.create_request_id()

    if len(files) == 0:
        raise HTTPException(status_code=422, detail="At least one file is required")
    if len(files) > settings.max_files:
        raise HTTPException(status_code=422, detail=f"Maximum {settings.max_files} files allowed")

    total_size = 0
    successes: list[tuple[str, bytes]] = []
    results: list[FileResult] = []

    for upload in files:
        original_name = upload.filename or "uploaded.pdf"
        if not _validate_pdf_name(original_name):
            results.append(
                FileResult(
                    original_name=original_name,
                    status=FileStatus.failed,
                    error_code="invalid_pdf",
                )
            )
            continue

        data = await upload.read()
        file_size = len(data)
        total_size += file_size

        if file_size > settings.max_file_size_mb * 1024 * 1024:
            results.append(
                FileResult(
                    original_name=original_name,
                    status=FileStatus.failed,
                    error_code="file_too_large",
                )
            )
            continue

        unlocked_bytes, error_code = unlock_pdf(data, password)
        if unlocked_bytes is None:
            logger.warning("Failed to unlock file: %s", error_code or "internal_error")
            results.append(
                FileResult(
                    original_name=original_name,
                    status=FileStatus.failed,
                    error_code=error_code or "internal_error",
                )
            )
            continue

        output_name = _unlocked_name(original_name)
        token = store.register_pdf(request_id=request_id, filename=output_name, payload=unlocked_bytes)
        successes.append((output_name, unlocked_bytes))
        results.append(
            FileResult(
                original_name=original_name,
                status=FileStatus.success,
                download_token=token,
            )
        )

    if total_size > settings.max_total_size_mb * 1024 * 1024:
        raise HTTPException(status_code=422, detail=f"Total request size exceeded {settings.max_total_size_mb}MB")

    succeeded = sum(1 for result in results if result.status == FileStatus.success)
    failed = len(results) - succeeded

    logger.info(
        "unlock_request request_id=%s total_files=%s succeeded=%s failed=%s total_size_bytes=%s",
        request_id,
        len(files),
        succeeded,
        failed,
        total_size,
    )

    if succeeded == 0:
        raise HTTPException(status_code=400, detail="All files failed to unlock")

    batch_download_available = False
    if len(successes) > 1:
        report = {
            "request_id": request_id,
            "results": [result.model_dump() for result in results],
        }
        store.register_batch_zip(request_id=request_id, files=successes, report=report)
        batch_download_available = True

    return UnlockResponse(
        request_id=request_id,
        summary=Summary(total=len(files), succeeded=succeeded, failed=failed),
        results=results,
        batch_download_available=batch_download_available,
    )


@app.get("/api/download/{request_id}/{download_token}")
def download_file(request_id: str, download_token: str) -> FileResponse:
    artifact = store.get_artifact(request_id=request_id, token=download_token)
    if artifact is None:
        raise HTTPException(status_code=404, detail="File not found or expired")
    return FileResponse(path=artifact.path, filename=artifact.filename, media_type=artifact.media_type)


@app.get("/api/download-batch/{request_id}")
def download_batch(request_id: str) -> FileResponse:
    artifact = store.get_batch_artifact(request_id=request_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Batch download not found or expired")
    return FileResponse(path=artifact.path, filename=artifact.filename, media_type=artifact.media_type)
