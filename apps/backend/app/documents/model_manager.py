"""Shared, locked and resumable embedding installation. Never invoked at app startup."""

import hashlib
import io
import json
import os
import shutil
import threading
import time
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout

from ..config import get_settings
from ..data_paths import embedding_home
from ..models import now
from .model_manifest import ARTIFACTS, REPO, REVISION

ACTIVE = {"CHECKING", "DOWNLOADING", "VERIFYING"}


class PreparationCancelled(Exception):
    pass


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def valid_artifact(path, specification):
    size, algorithm, expected = specification
    if not path.is_file() or path.stat().st_size != size:
        return False
    digest = hashlib.sha256() if algorithm == "sha256" else hashlib.sha1()
    if algorithm == "git-sha1":
        digest.update(f"blob {size}\0".encode())
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == expected


class EmbeddingModelManager:
    def __init__(self, settings=None, downloader=None, smoke=None, artifacts=None):
        self.settings = settings or get_settings()
        self.home = embedding_home(self.settings)
        self.artifacts = artifacts or ARTIFACTS
        self.total = sum(item[0] for item in self.artifacts.values())
        self.downloader = downloader or self.download
        self.smoke = smoke or self.smoke_model
        self._verified_stamp = None
        self._thread = None

    def patch(self, **values):
        self.home.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.home / "state.lock"), timeout=10):
            state = read_json(self.home / "state.json")
            state.update(values, updated_at=now().isoformat())
            atomic_json(self.home / "state.json", state)

    def installation(self):
        pointer = read_json(self.home / "active.json")
        key = pointer.get("installation", "")
        if not isinstance(key, str) or len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
            return None
        root = self.home / "installs" / key
        manifest = read_json(root / "ready.json")
        if (
            manifest.get("revision") != REVISION
            or manifest.get("verification") != "hashes-and-embedding-smoke"
        ):
            return None
        return root

    def ready_path(self):
        root = self.installation()
        if not root:
            return None
        try:
            stamp = tuple(
                (name, (root / name).stat().st_mtime_ns, (root / name).stat().st_size)
                for name in self.artifacts
            )
            if self._verified_stamp != (root, stamp):
                if not all(valid_artifact(root / name, spec) for name, spec in self.artifacts.items()):
                    return None
                self.smoke(root)
                self._verified_stamp = (root, stamp)
            return root
        except Exception:
            return None

    def status(self, actor=None, admin=False):
        state = read_json(self.home / "state.json")
        phase = state.get("state", "NOT_INSTALLED")
        if phase in ACTIVE:
            try:
                with FileLock(str(self.home / "download.lock"), timeout=0):
                    self.patch(state="CANCELLED", error="Подготовка прервана. Можно продолжить загрузку.")
                    state = read_json(self.home / "state.json")
                    phase = "CANCELLED"
            except Timeout:
                pass
        ready = self.ready_path()
        if ready and phase not in ACTIVE:
            phase = "READY"
        elif phase == "READY" and not ready:
            phase = "FAILED"
            state["error"] = "Файлы модели повреждены. Нажмите «Повторить»."
        return {
            "state": phase,
            "model": "intfloat/multilingual-e5-small",
            "revision": REVISION,
            "dimension": 384,
            "downloaded_bytes": self.total if ready else state.get("downloaded_bytes", 0),
            "total_bytes": self.total,
            "ready": bool(ready),
            "error": state.get("error") if phase != "READY" else None,
            "cancel_requested": (self.home / "cancel").exists() and phase in ACTIVE,
            "can_cancel": phase in ACTIVE and (admin or actor == state.get("actor")),
        }

    def prepare(self, actor=None):
        self.home.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self.home / "download.lock"), timeout=0, thread_local=False)
        try:
            lock.acquire()
        except Timeout:
            return self.status(actor)
        if self.ready_path():
            lock.release()
            return self.status(actor)
        (self.home / "cancel").unlink(missing_ok=True)
        self.patch(state="CHECKING", downloaded_bytes=0, error=None, actor=actor)
        self._thread = threading.Thread(target=self.run, args=(lock,), daemon=True)
        self._thread.start()
        return self.status(actor)

    def cancel(self, actor=None, admin=False):
        state = read_json(self.home / "state.json")
        if actor != state.get("actor") and not admin:
            raise PermissionError("Отменить может инициатор или администратор")
        if state.get("state") in ACTIVE:
            (self.home / "cancel").touch()
        return self.status(actor, admin)

    def check_cancel(self):
        if (self.home / "cancel").exists():
            raise PreparationCancelled()

    def download(self, name, stage, progress):
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils.tqdm import tqdm

        manager = self

        class Progress(tqdm):
            def __init__(self, *args, **kwargs):
                kwargs.update(file=io.StringIO(), disable=False)
                super().__init__(*args, **kwargs)
                progress(self.n)

            def update(self, n=1):
                manager.check_cancel()
                result = super().update(n)
                progress(self.n)
                return result

        hf_hub_download(REPO, name, revision=REVISION, local_dir=stage, tqdm_class=Progress, token=False)

    def smoke_model(self, root):
        from .embedding import LocalEmbeddingProvider, validate_vectors

        provider = LocalEmbeddingProvider(self.settings, verified_root=root)
        validate_vectors(provider.embed(["Где находится библиотека?"], query=True), 1, 384)
        validate_vectors(provider.embed(["The library is in Seoul."]), 1, 384)

    def run(self, lock):
        stage = self.home / "staging" / REVISION
        try:
            if shutil.disk_usage(self.home).free < self.total * 3 + 64 * 1024 * 1024:
                raise OSError("disk_space")
            stage.mkdir(parents=True, exist_ok=True)
            legacy = Path(self.settings.embedding_model_dir or ".data/embeddings/e5-small").resolve()
            completed = 0
            for name, spec in self.artifacts.items():
                self.check_cancel()
                target = stage / name
                if not valid_artifact(target, spec) and valid_artifact(legacy / name, spec):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(legacy / name, target)
                if not valid_artifact(target, spec):
                    # A complete but invalid artifact must not be accepted from the local cache.
                    target.unlink(missing_ok=True)
                    metadata = stage / ".cache/huggingface/download" / (name + ".metadata")
                    metadata.unlink(missing_ok=True)
                    self.patch(state="DOWNLOADING", downloaded_bytes=completed)
                    last_write = [0.0]

                    def progress(count):
                        self.check_cancel()
                        if time.monotonic() - last_write[0] >= 0.2:
                            self.patch(downloaded_bytes=completed + min(int(count), spec[0]))
                            last_write[0] = time.monotonic()

                    self.downloader(name, stage, progress)
                self.check_cancel()
                if not valid_artifact(target, spec):
                    raise ValueError("verification")
                completed += spec[0]
                self.patch(downloaded_bytes=completed)
            self.patch(state="VERIFYING")
            self.smoke(stage)
            self.check_cancel()
            atomic_json(
                stage / "ready.json",
                {
                    "provider": "huggingface_hub",
                    "repo": REPO,
                    "revision": REVISION,
                    "dimension": 384,
                    "artifacts": self.artifacts,
                    "total_bytes": self.total,
                    "downloaded_at": now().isoformat(),
                    "verification": "hashes-and-embedding-smoke",
                },
            )
            key = uuid4().hex
            installs = self.home / "installs"
            installs.mkdir(exist_ok=True)
            os.replace(stage, installs / key)
            atomic_json(self.home / "active.json", {"installation": key})
            self.patch(state="READY", error=None)
        except PreparationCancelled:
            self.patch(state="CANCELLED", error=None)
        except Exception as error:
            if isinstance(error, OSError) and str(error) == "disk_space":
                reason = "Недостаточно места: требуется запас для staging и проверки модели."
            elif isinstance(error, ValueError):
                reason = "Проверка целостности или embedding smoke не пройдена. Повторите подготовку."
            else:
                reason = (
                    "Не удалось подготовить поиск по файлам. Проверьте интернет и доступ к каталогу данных."
                )
            self.patch(state="FAILED", error=reason)
        finally:
            lock.release()


@lru_cache
def get_model_manager():
    return EmbeddingModelManager()
