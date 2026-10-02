import os
import time
from pathlib import Path

from app.services.storage import ArtifactStore


def test_workers_share_session_expiry_and_batch_downloads(tmp_path: Path, monkeypatch) -> None:
    now = time.time()
    monkeypatch.setattr("app.services.storage.time.time", lambda: now)
    first = ArtifactStore(str(tmp_path), ttl_seconds=300)
    second = ArtifactStore(str(tmp_path), ttl_seconds=300)
    request_id = first.create_request_id()
    token = first.register_pdf(request_id, "session.pdf", b"pdf", ttl_seconds=3600)
    batch = first.register_batch_zip(request_id, [("session.pdf", b"pdf")], {})
    assert second.get_artifact(request_id, token).path.read_bytes() == b"pdf"
    assert second.get_batch_artifact(request_id).path.name == f"{batch}.zip"
    now += 301
    second.purge_now()
    assert first.get_artifact(request_id, token).path.read_bytes() == b"pdf"
    assert second.get_batch_artifact(request_id) is None
    restarted = ArtifactStore(str(tmp_path), ttl_seconds=300)
    assert restarted.get_artifact(request_id, token) is not None
    now += 3300
    restarted.purge_now()
    assert first.get_artifact(request_id, token) is None
    assert not (tmp_path / request_id).exists()


def test_cleanup_retries_file_that_is_still_open(tmp_path: Path, monkeypatch) -> None:
    store = ArtifactStore(str(tmp_path), ttl_seconds=300)
    request_id = store.create_request_id()
    token = store.register_pdf(request_id, "test.pdf", b"pdf", ttl_seconds=-1)
    original = Path.unlink
    def deny_pdf_unlink(path, *args, **kwargs):
        if path.suffix == ".pdf":
            raise PermissionError("file in use")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", deny_pdf_unlink)
        store.purge_now()
        assert list(tmp_path.glob("*/*.pdf"))
    store.purge_now()
    assert list(tmp_path.glob("*/*.pdf")) == []


def test_expired_artifact_is_removed_after_store_restart(tmp_path: Path) -> None:
    first_store = ArtifactStore(base_dir=str(tmp_path), ttl_seconds=1)
    request_id = first_store.create_request_id()
    token = first_store.register_pdf(request_id, "unlocked.pdf", b"pdf")
    artifact = first_store.get_artifact(request_id, token)
    assert artifact is not None

    old_time = time.time() - 10
    os.utime(artifact.path, (old_time, old_time))
    with first_store._transaction() as database:
        database.execute("UPDATE artifacts SET expires_at=? WHERE token=?", (old_time, token))

    ArtifactStore(base_dir=str(tmp_path), ttl_seconds=1)

    assert not artifact.path.exists()
    assert not (tmp_path / request_id).exists()


def test_expiring_one_artifact_keeps_other_request_files(tmp_path: Path) -> None:
    store = ArtifactStore(base_dir=str(tmp_path), ttl_seconds=300)
    request_id = store.create_request_id()
    first_token = store.register_pdf(request_id, "same.pdf", b"first")
    second_token = store.register_pdf(request_id, "same.pdf", b"second")
    with store._transaction() as database:
        database.execute("UPDATE artifacts SET expires_at=? WHERE token=?", (time.time() - 1, first_token))

    store.purge_now()

    assert store.get_artifact(request_id, first_token) is None
    second = store.get_artifact(request_id, second_token)
    assert second is not None
    assert second.path.read_bytes() == b"second"
