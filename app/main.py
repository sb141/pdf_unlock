from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.models import FileResult, FileStatus, Summary, UnlockResponse
from app.services.pdf_unlocker import unlock_pdf
from app.services.storage import ArtifactStore


logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger("pdf_unlock")

store = ArtifactStore(base_dir=settings.temp_dir, ttl_seconds=settings.download_ttl_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    async def purge_expired() -> None:
        while True:
            await run_in_threadpool(store.purge_now)
            await asyncio.sleep(max(1, min(settings.download_ttl_seconds, 60)))

    cleanup_task = asyncio.create_task(purge_expired())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(title="PDF Unlock API", version="1.0.0", lifespan=lifespan)


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


async def _upload_size(upload: UploadFile, total_size: int, total_limit: int) -> tuple[int, int]:
    """Measure an upload without loading it all into memory."""
    file_size = 0
    while chunk := await upload.read(1024 * 1024):
        file_size += len(chunk)
        total_size += len(chunk)
        if total_size > total_limit:
            raise HTTPException(status_code=422, detail=f"Total request size exceeded {settings.max_total_size_mb}MB")
    await upload.seek(0)
    return file_size, total_size


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
    file_sizes: list[int] = []
    total_limit = settings.max_total_size_mb * 1024 * 1024
    file_limit = settings.max_file_size_mb * 1024 * 1024
    for upload in files:
        file_size, total_size = await _upload_size(upload, total_size, total_limit)
        file_sizes.append(file_size)

    successes: list[tuple[str, bytes]] = []
    results: list[FileResult] = []
    output_names: set[str] = set()

    for upload, file_size in zip(files, file_sizes):
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

        if file_size > file_limit:
            results.append(
                FileResult(
                    original_name=original_name,
                    status=FileStatus.failed,
                    error_code="file_too_large",
                )
            )
            continue

        data = await upload.read()
        unlocked_bytes, error_code = await run_in_threadpool(unlock_pdf, data, password)
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
        if output_name.casefold() in output_names:
            stem = Path(output_name).stem
            suffix = 2
            while f"{stem}_{suffix}.pdf".casefold() in output_names:
                suffix += 1
            output_name = f"{stem}_{suffix}.pdf"
        output_names.add(output_name.casefold())
        token = await run_in_threadpool(store.register_pdf, request_id, output_name, unlocked_bytes)
        successes.append((output_name, unlocked_bytes))
        results.append(
            FileResult(
                original_name=original_name,
                status=FileStatus.success,
                download_token=token,
            )
        )

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
        await run_in_threadpool(store.register_batch_zip, request_id, successes, report)
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
