"""Optional real CPU smoke: never downloads; deterministic tests run without the cache."""

from pathlib import Path

import numpy as np
import pytest

from app.config import get_settings
from app.documents.embedding import get_embedding


@pytest.mark.skipif(
    not (Path(get_settings().embedding_model_dir) / "onnx/model_quantized.onnx").is_file(),
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
