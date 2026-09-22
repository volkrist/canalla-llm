"""Pinned quantized E5 on CPU. Runtime never downloads or executes remote code."""

import threading
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..config import get_settings

if TYPE_CHECKING:  # The model stack stays lazily imported: it is never loaded at startup.
    from fastembed import TextEmbedding
    from tokenizers import Tokenizer

REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
MODEL = "intfloat/multilingual-e5-small"


class EmbeddingUnavailable(Exception):
    pass


class EmbeddingProvider(ABC):
    dimension = 384
    version = MODEL + "@" + REVISION + ":int8"

    @abstractmethod
    def embed(self, texts: list[str], query=False) -> list[list[float]]: ...

    @abstractmethod
    def token_count(self, text: str) -> int: ...


def validate_vectors(vectors, count, dimension):
    values = np.asarray(vectors, dtype=np.float32)
    if values.shape != (count, dimension) or not np.isfinite(values).all():
        raise ValueError("Invalid embedding dimensions or values")
    norms = np.linalg.norm(values, axis=1)
    if np.any(norms == 0):
        raise ValueError("Empty embedding")
    return (values / norms[:, None]).tolist()


class LocalEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings=None, *, verified_root: Path | None = None):
        self.settings = settings or get_settings()
        # Only the lifecycle manager supplies a staging root for its verification smoke.
        self._verified_root = verified_root
        self._model: TextEmbedding | None = None
        self._tokenizer: Tokenizer | None = None
        self._lock = threading.RLock()

    def load(self):
        with self._lock:
            if self._model is not None:
                return
            from .model_manager import get_model_manager

            root = self._verified_root or get_model_manager().ready_path()
            if (
                self.settings.embedding_model_name != MODEL
                or root is None
                or not (root / "onnx/model_quantized.onnx").is_file()
            ):
                raise EmbeddingUnavailable("Embedding service unavailable. Подготовьте локальную модель.")
            try:
                from fastembed import TextEmbedding
                from fastembed.common.model_description import ModelSource, PoolingType
                from tokenizers import Tokenizer

                if not any(m["model"] == MODEL for m in TextEmbedding.list_supported_models()):
                    TextEmbedding.add_custom_model(
                        model=MODEL,
                        pooling=PoolingType.MEAN,
                        normalization=True,
                        sources=ModelSource(hf="Xenova/multilingual-e5-small"),
                        dim=self.dimension,
                        model_file="onnx/model_quantized.onnx",
                        license="mit",
                    )
                self._tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
                self._tokenizer.no_truncation()
                self._model = TextEmbedding(
                    MODEL,
                    specific_model_path=str(root),
                    local_files_only=True,
                    threads=self.settings.embedding_threads,
                    providers=["CPUExecutionProvider"],
                    cuda=False,
                )
            except Exception as error:
                raise EmbeddingUnavailable(
                    "Embedding service unavailable. Проверьте локальную модель."
                ) from error

    def loaded(self) -> "tuple[TextEmbedding, Tokenizer]":
        """The model and tokenizer, which `load()` either fills or fails on."""
        self.load()
        if self._model is None or self._tokenizer is None:
            raise EmbeddingUnavailable("Embedding service unavailable. Подготовьте локальную модель.")
        return self._model, self._tokenizer

    def token_count(self, text):
        _model, tokenizer = self.loaded()
        return len(tokenizer.encode(text).ids)

    def embed(self, texts, query=False):
        model, _tokenizer = self.loaded()
        prefix = "query: " if query else "passage: "
        prepared = [prefix + text for text in texts]
        if any(self.token_count(text) > 512 for text in prepared):
            raise ValueError("Embedding input exceeds 512 tokens")
        try:
            with self._lock:
                return validate_vectors(list(model.embed(prepared, batch_size=8)), len(texts), self.dimension)
        except Exception as error:
            raise EmbeddingUnavailable(
                "Embedding service unavailable. Не удалось вычислить векторы."
            ) from error


@lru_cache
def get_embedding():
    return LocalEmbeddingProvider()


if __name__ == "__main__":
    from .model_manager import get_model_manager

    manager = get_model_manager()
    manager.prepare("local-administrator")
    if manager._thread:
        manager._thread.join()
    print(manager.status())
