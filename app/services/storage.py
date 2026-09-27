from __future__ import annotations

import json
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock


@dataclass
class Artifact:
    request_id: str
    path: Path
    filename: str
    media_type: str
    expires_at: float


class ArtifactStore:
    def __init__(self, base_dir: str, ttl_seconds: int) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self._lock = Lock()
        self._artifacts: dict[str, Artifact] = {}
        self._batch_token_by_request: dict[str, str] = {}
        self.purge_now()

    def create_request_id(self) -> str:
        return uuid.uuid4().hex

    def register_pdf(self, request_id: str, filename: str, payload: bytes) -> str:
        token = uuid.uuid4().hex
        request_dir = self.base_dir / request_id
        request_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename).name
        output_path = request_dir / f"{token}.pdf"
        output_path.write_bytes(payload)

        artifact = Artifact(
            request_id=request_id,
            path=output_path,
            filename=safe_name,
            media_type="application/pdf",
            expires_at=time.time() + self.ttl_seconds,
        )

        with self._lock:
            self._purge_expired_locked()
            self._artifacts[token] = artifact

        return token

    def register_batch_zip(self, request_id: str, files: list[tuple[str, bytes]], report: dict) -> str:
        token = uuid.uuid4().hex
        request_dir = self.base_dir / request_id
        request_dir.mkdir(parents=True, exist_ok=True)
        zip_path = request_dir / f"{token}.zip"

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files:
                archive.writestr(Path(name).name, content)
            archive.writestr("report.json", json.dumps(report, indent=2))

        artifact = Artifact(
            request_id=request_id,
            path=zip_path,
            filename="unlocked_batch.zip",
            media_type="application/zip",
            expires_at=time.time() + self.ttl_seconds,
        )

        with self._lock:
            self._purge_expired_locked()
            self._artifacts[token] = artifact
            self._batch_token_by_request[request_id] = token

        return token

    def get_artifact(self, request_id: str, token: str) -> Artifact | None:
        with self._lock:
            self._purge_expired_locked()
            artifact = self._artifacts.get(token)
            if artifact is None or artifact.request_id != request_id:
                return None
            return artifact

    def get_batch_artifact(self, request_id: str) -> Artifact | None:
        with self._lock:
            self._purge_expired_locked()
            token = self._batch_token_by_request.get(request_id)
            if token is None:
                return None
            return self._artifacts.get(token)

    def _purge_expired_locked(self) -> None:
        now = time.time()
        expired_tokens = [token for token, artifact in self._artifacts.items() if artifact.expires_at <= now]
        for token in expired_tokens:
            artifact = self._artifacts.pop(token)
            artifact.path.unlink(missing_ok=True)
            if self._batch_token_by_request.get(artifact.request_id) == token:
                self._batch_token_by_request.pop(artifact.request_id, None)

        active_requests = {artifact.request_id for artifact in self._artifacts.values()}
        for request_dir in self.base_dir.iterdir():
            if not request_dir.is_dir() or request_dir.name in active_requests:
                continue
            if len(request_dir.name) != 32 or any(c not in "0123456789abcdef" for c in request_dir.name):
                continue
            paths = list(request_dir.iterdir())
            newest_mtime = max((path.stat().st_mtime for path in paths), default=request_dir.stat().st_mtime)
            if newest_mtime + self.ttl_seconds > now:
                continue
            for path in paths:
                if path.is_file():
                    path.unlink(missing_ok=True)
            request_dir.rmdir()

    def purge_now(self) -> None:
        with self._lock:
            self._purge_expired_locked()

    def clear_all(self) -> None:
        with self._lock:
            self._artifacts.clear()
            self._batch_token_by_request.clear()
            if self.base_dir.exists():
                for child in self.base_dir.glob("*"):
                    if child.is_dir():
                        for path in child.glob("*"):
                            if path.is_file():
                                path.unlink(missing_ok=True)
                        child.rmdir()


def default_temp_dir() -> str:
    return tempfile.gettempdir()
