from enum import Enum
from typing import Optional

from pydantic import BaseModel


class FileStatus(str, Enum):
    success = "success"
    failed = "failed"


class FileResult(BaseModel):
    original_name: str
    status: FileStatus
    error_code: Optional[str] = None
    download_token: Optional[str] = None


class Summary(BaseModel):
    total: int
    succeeded: int
    failed: int


class UnlockResponse(BaseModel):
    request_id: str
    summary: Summary
    results: list[FileResult]
    batch_download_available: bool = False
