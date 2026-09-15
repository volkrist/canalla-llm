import hashlib
import threading
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.documents.embedding import EmbeddingUnavailable, LocalEmbeddingProvider, validate_vectors
from app.documents.model_manager import EmbeddingModelManager, atomic_json
from app.documents.model_manifest import REVISION


@pytest.fixture
def factory(tmp_path):
    def make(downloader=None, smoke=None):
        data = b"verified-test-artifact"
        settings = get_settings().model_copy(
            update={
                "alex_llm_data_dir": str(tmp_path / "app"),
                "embedding_model_dir": str(tmp_path / "legacy"),
            }
        )

        def download(name, stage, progress):
            (stage / name).write_bytes(data)
            progress(len(data))

        return EmbeddingModelManager(
            settings,
            downloader or download,
            smoke or (lambda root: None),
            {"model.bin": (len(data), "sha256", hashlib.sha256(data).hexdigest())},
        )

    return make


def finish(manager):
    manager._thread.join(5)
    assert not manager._thread.is_alive()
    return manager.status()


def test_missing_prepare_hashes_activation_and_offline_restart(factory):
    manager = factory()
    assert manager.status()["state"] == "NOT_INSTALLED"
    manager.prepare("alice")
    assert finish(manager)["state"] == "READY"
    assert manager.status()["downloaded_bytes"] == manager.total
    root = manager.ready_path()
    assert root and (root / "ready.json").is_file()
    other = factory(lambda *_: pytest.fail("offline must not download"))
    assert other.status()["state"] == "READY"
    assert other.prepare("bob")["ready"]
    assert other.ready_path() == root


def test_progress_lock_cancel_resume_and_owner(factory):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def download(name, stage, progress):
        calls.append(name)
        (stage / "partial").write_bytes(b"partial")
        progress(7)
        entered.set()
        assert release.wait(3)
        progress(8)

    manager = factory(download)
    manager.prepare("alice")
    assert entered.wait(3)
    assert manager.status()["downloaded_bytes"] == 7
    other = factory(download)
    assert other.prepare("bob")["state"] == "DOWNLOADING"
    assert not other.status("bob")["can_cancel"]
    with pytest.raises(PermissionError):
        other.cancel("bob")
    manager.cancel("alice")
    release.set()
    assert finish(manager)["state"] == "CANCELLED"
    assert len(calls) == 1
    # The official downloader owns partial/resume files; the manager preserves them.
    assert (manager.home / "staging" / REVISION / "partial").read_bytes() == b"partial"
    retry = factory()
    retry.prepare("alice")
    assert finish(retry)["ready"]


@pytest.mark.parametrize("failure", ["corrupt", "dimension", "offline"])
def test_failure_retry_without_restart(factory, failure):
    manager = factory()
    good_download, good_smoke = manager.downloader, manager.smoke
    if failure == "corrupt":
        manager.downloader = lambda name, stage, progress: (stage / name).write_bytes(b"bad")
    elif failure == "dimension":
        manager.smoke = lambda root: validate_vectors([[1, 2]], 1, 384)
    else:

        def offline(*args):
            raise ConnectionError("secret-provider-debug")

        manager.downloader = offline
    manager.prepare("alice")
    status = finish(manager)
    assert status["state"] == "FAILED" and not status["ready"]
    assert "secret-provider-debug" not in str(status)
    assert not (manager.home / "active.json").exists()
    manager.downloader, manager.smoke = good_download, good_smoke
    manager.prepare("alice")
    assert finish(manager)["ready"]


def test_disk_failure_is_local(factory, monkeypatch):
    manager = factory(lambda *_: pytest.fail("must not download"))
    monkeypatch.setattr("app.documents.model_manager.shutil.disk_usage", lambda _: SimpleNamespace(free=1))
    manager.prepare("alice")
    assert "Недостаточно места" in finish(manager)["error"]


def test_crash_and_corrupted_ready(factory):
    manager = factory()
    atomic_json(manager.home / "state.json", {"state": "DOWNLOADING"})
    assert manager.status()["state"] == "CANCELLED"
    manager.prepare("alice")
    assert finish(manager)["ready"]
    (manager.ready_path() / "model.bin").write_bytes(b"corrupt")
    assert manager.status()["state"] == "FAILED"
    manager.prepare("alice")
    assert finish(manager)["ready"]


def test_verified_legacy_import_no_network(factory):
    manager = factory(lambda *_: pytest.fail("must import"))
    from pathlib import Path

    legacy = Path(manager.settings.embedding_model_dir)
    legacy.mkdir()
    (legacy / "model.bin").write_bytes(b"verified-test-artifact")
    manager.prepare()
    assert finish(manager)["ready"]


def test_api_auth_ownership_and_shared_state(factory, client, auth, monkeypatch):
    manager = factory()
    monkeypatch.setattr("app.documents.routes.get_model_manager", lambda: manager)
    alice, bob = auth(), auth("bob@example.com")
    assert client.get("/rag/model").status_code == 401
    assert client.get("/rag/model", headers=alice).json()["state"] == "NOT_INSTALLED"
    assert client.post("/rag/model/prepare", headers=alice).status_code == 200
    finish(manager)
    assert client.get("/rag/model", headers=bob).json()["ready"]
    assert client.post("/rag/model/cancel", headers=bob).status_code == 403
    assert "actor" not in client.get("/rag/model", headers=bob).text


def test_provider_refuses_partial_and_chat_still_works(factory, client, auth, monkeypatch):
    manager = factory()
    monkeypatch.setattr("app.documents.model_manager.get_model_manager", lambda: manager)
    with pytest.raises(EmbeddingUnavailable):
        LocalEmbeddingProvider().embed(["hello"])
    headers = auth()
    chat = client.post("/chats", headers=headers, json={"title": "Offline"}).json()
    response = client.post(f"/chats/{chat['id']}/stream", headers=headers, json={"content": "Привет"})
    assert response.status_code == 200
    assert "event: done" in response.text and "event: delta" in response.text
    messages = client.get(f"/chats/{chat['id']}/messages", headers=headers).json()
    assert messages[-1]["role"] == "assistant" and messages[-1]["content"]
