import os
import time
from pathlib import Path

from app.services.storage import ArtifactStore


def test_expired_orphan_is_removed_after_store_restart(tmp_path: Path) -> None:
    first_store = ArtifactStore(base_dir=str(tmp_path), ttl_seconds=1)
    request_id = first_store.create_request_id()
    token = first_store.register_pdf(request_id, "unlocked.pdf", b"pdf")
    artifact = first_store.get_artifact(request_id, token)
    assert artifact is not None

    old_time = time.time() - 10
    os.utime(artifact.path, (old_time, old_time))

    ArtifactStore(base_dir=str(tmp_path), ttl_seconds=1)

    assert not artifact.path.exists()
    assert not (tmp_path / request_id).exists()


def test_expiring_one_artifact_keeps_other_request_files(tmp_path: Path) -> None:
    store = ArtifactStore(base_dir=str(tmp_path), ttl_seconds=300)
    request_id = store.create_request_id()
    first_token = store.register_pdf(request_id, "same.pdf", b"first")
    second_token = store.register_pdf(request_id, "same.pdf", b"second")
    store._artifacts[first_token].expires_at = time.time() - 1

    store.purge_now()

    assert store.get_artifact(request_id, first_token) is None
    second = store.get_artifact(request_id, second_token)
    assert second is not None
    assert second.path.read_bytes() == b"second"
