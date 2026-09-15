"""Optional real CPU smoke: never downloads; deterministic tests run without the cache."""

from pathlib import Path

import numpy as np
import pytest

from app.config import get_settings
from app.documents.embedding import get_embedding
from app.documents.model_manager import EmbeddingModelManager


@pytest.fixture(scope="module", autouse=True)
def verified_local_model():
    legacy = Path(get_settings().embedding_model_dir or ".data/embeddings/e5-small")
    if not (legacy / "onnx/model_quantized.onnx").is_file():
        pytest.skip("Local embedding cache not prepared; this test never downloads")

    def no_network(*args):
        pytest.fail("CPU smoke must only import verified local artifacts")

    manager = EmbeddingModelManager(downloader=no_network)
    manager.prepare("cpu-test")
    if manager._thread:
        manager._thread.join(90)
        assert not manager._thread.is_alive()
    assert manager.status()["ready"]


@pytest.mark.skipif(
    not (
        Path(get_settings().embedding_model_dir or ".data/embeddings/e5-small") / "onnx/model_quantized.onnx"
    ).is_file(),
    reason="Local embedding cache not prepared; this test never downloads",
)
@pytest.mark.parametrize(
    "query",
    [
        "Где Аврора хранит резервные копии?",
        "Where are Aurora backups stored?",
        "Aurora 백업은 어디에 저장됩니까?",
    ],
)
def test_real_multilingual_retrieval(query):
    embedding = get_embedding()
    documents = np.asarray(
        embedding.embed(
            [
                "Aurora backups are stored in Seoul for seventeen days.",
                "Banana trees grow in a tropical orchard.",
                "The train leaves the station at noon.",
            ]
        )
    )
    vector = np.asarray(embedding.embed([query], query=True)[0])
    scores = documents @ vector
    assert documents.shape == (3, 384)
    assert np.allclose(np.linalg.norm(documents, axis=1), 1)
    assert scores[0] > 0.72
    assert max(scores[1:]) < 0.72
