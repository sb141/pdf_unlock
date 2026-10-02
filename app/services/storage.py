from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Artifact:
    request_id: str
    path: Path
    filename: str
    media_type: str
    expires_at: float


def _valid_id(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


class ArtifactStore:
    """Share artifact metadata and serialize file cleanup across worker processes."""

    def __init__(self, base_dir: str, ttl_seconds: int) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self._database = self.base_dir / "artifacts.sqlite3"
        with self._transaction() as database:
            database.execute("""CREATE TABLE IF NOT EXISTS artifacts (
                token TEXT PRIMARY KEY, request_id TEXT NOT NULL, path TEXT NOT NULL,
                filename TEXT NOT NULL, media_type TEXT NOT NULL, expires_at REAL NOT NULL
            )""")
            database.execute("CREATE INDEX IF NOT EXISTS artifacts_request ON artifacts(request_id)")
        self.purge_now()

    @contextmanager
    def _transaction(self):
        database = sqlite3.connect(self._database, timeout=30)
        database.row_factory = sqlite3.Row
        try:
            database.execute("BEGIN IMMEDIATE")
            yield database
            database.commit()
        except BaseException:
            database.rollback()
            raise
        finally:
            database.close()

    def create_request_id(self) -> str:
        return uuid.uuid4().hex

    def _register(self, request_id: str, filename: str, media_type: str, ttl: int, write) -> str:
        if not _valid_id(request_id):
            raise ValueError("Invalid request ID")
        token = uuid.uuid4().hex
        extension = ".pdf" if media_type == "application/pdf" else ".zip"
        relative = Path(request_id) / f"{token}{extension}"
        output_path = self.base_dir / relative
        # Every writer and purger holds the same database lock while touching files.
        with self._transaction() as database:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                write(output_path)
                database.execute("INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?)", (
                    token, request_id, str(relative), Path(filename).name, media_type, time.time() + ttl,
                ))
            except BaseException:
                output_path.unlink(missing_ok=True)
                raise
        return token

    def register_pdf(self, request_id: str, filename: str, payload: bytes, ttl_seconds: int | None = None) -> str:
        return self._register(request_id, filename, "application/pdf",
                              self.ttl_seconds if ttl_seconds is None else ttl_seconds,
                              lambda path: path.write_bytes(payload))

    def register_batch_zip(self, request_id: str, files: list[tuple[str, bytes]], report: dict) -> str:
        def write(path: Path) -> None:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, content in files:
                    archive.writestr(Path(name).name, content)
                archive.writestr("report.json", json.dumps(report, indent=2))
        return self._register(request_id, "unlocked_batch.zip", "application/zip", self.ttl_seconds, write)

    def _artifact(self, row) -> Artifact | None:
        if row is None:
            return None
        path = self.base_dir / row["path"]
        if not path.is_file():
            return None
        return Artifact(row["request_id"], path, row["filename"], row["media_type"], row["expires_at"])

    def get_artifact(self, request_id: str, token: str) -> Artifact | None:
        with self._transaction() as database:
            row = database.execute(
                "SELECT * FROM artifacts WHERE token=? AND request_id=? AND expires_at>?",
                (token, request_id, time.time()),
            ).fetchone()
            return self._artifact(row)

    def get_batch_artifact(self, request_id: str) -> Artifact | None:
        with self._transaction() as database:
            row = database.execute(
                "SELECT * FROM artifacts WHERE request_id=? AND media_type='application/zip' "
                "AND expires_at>? ORDER BY expires_at DESC LIMIT 1", (request_id, time.time()),
            ).fetchone()
            return self._artifact(row)

    def purge_now(self) -> None:
        with self._transaction() as database:
            now = time.time()
            for row in database.execute("SELECT token, path FROM artifacts WHERE expires_at<=?", (now,)).fetchall():
                try:
                    (self.base_dir / row["path"]).unlink(missing_ok=True)
                except OSError:
                    # A download may still have the file open on Windows; retry next pass.
                    continue
                database.execute("DELETE FROM artifacts WHERE token=?", (row["token"],))
            registered = {row["path"] for row in database.execute("SELECT path FROM artifacts")}
            for request_dir in self.base_dir.iterdir():
                if not request_dir.is_dir() or not _valid_id(request_dir.name):
                    continue
                for path in request_dir.iterdir():
                    if str(path.relative_to(self.base_dir)) in registered or not path.is_file():
                        continue
                    try:
                        if path.stat().st_mtime + self.ttl_seconds <= now:
                            path.unlink(missing_ok=True)
                    except OSError:
                        continue
                try:
                    request_dir.rmdir()
                except OSError:
                    pass  # Nonempty directories still contain active files.

    def clear_all(self) -> None:
        with self._transaction() as database:
            for child in self.base_dir.iterdir():
                if child.is_dir() and _valid_id(child.name):
                    for path in child.iterdir():
                        if path.is_file():
                            path.unlink(missing_ok=True)
                    child.rmdir()
            database.execute("DELETE FROM artifacts")


def default_temp_dir() -> str:
    return tempfile.gettempdir()
