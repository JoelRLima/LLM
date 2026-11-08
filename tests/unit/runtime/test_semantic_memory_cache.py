import json
import os
import pickle

import pytest

from llm_agent.agent.memory.semantic_memory import SemanticMemory


class _Memory:
    state = {"file_summaries": {}}


class _TouchMarker:
    def __reduce__(self):
        return os.system, ("echo executed > semantic-cache-marker",)


def test_load_index_does_not_execute_pickle_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "semantic-index.pkl"
    cache.write_bytes(pickle.dumps(_TouchMarker()))
    marker = tmp_path / "semantic-cache-marker"

    assert SemanticMemory(_Memory(), model_name="test-model").load_index(str(cache)) is False
    assert marker.exists() is False


def test_load_index_accepts_valid_data_only_cache(tmp_path):
    np = pytest.importorskip("numpy")
    cache = tmp_path / "semantic-index.json"
    cache.write_text(
        json.dumps(
            {
                "format_version": 1,
                "model_name": "test-model",
                "paths": ["src/example.py", "tests/example.py"],
                "embeddings": [[1.0, 0.0], [0.0, 1.0]],
            }
        ),
        encoding="utf-8",
    )
    index = SemanticMemory(_Memory(), model_name="test-model")

    assert index.load_index(str(cache)) is True
    assert index._paths == ["src/example.py", "tests/example.py"]
    assert np.array_equal(index._embeddings, [[1.0, 0.0], [0.0, 1.0]])


def test_load_index_rejects_malformed_embedding_without_mutating_state(tmp_path):
    cache = tmp_path / "semantic-index.json"
    cache.write_text(
        json.dumps(
            {
                "format_version": 1,
                "model_name": "test-model",
                "paths": ["src/example.py"],
                "embeddings": [[1.0], [2.0]],
            }
        ),
        encoding="utf-8",
    )
    index = SemanticMemory(_Memory(), model_name="test-model")

    assert index.load_index(str(cache)) is False
    assert index._paths == []
    assert index._embeddings is None


def test_load_index_rejects_boolean_cache_version(tmp_path):
    cache = tmp_path / "semantic-index.json"
    cache.write_text(
        json.dumps(
            {
                "format_version": True,
                "model_name": "test-model",
                "paths": [],
                "embeddings": None,
            }
        ),
        encoding="utf-8",
    )

    assert SemanticMemory(_Memory(), model_name="test-model").load_index(str(cache)) is False


def test_save_index_writes_versioned_json_cache(tmp_path):
    cache = tmp_path / "semantic-index.json"
    index = SemanticMemory(_Memory(), model_name="test-model")

    index.save_index(str(cache))

    assert json.loads(cache.read_text(encoding="utf-8")) == {
        "format_version": 1,
        "model_name": "test-model",
        "paths": [],
        "embeddings": None,
    }


def test_embedding_cache_roundtrip(tmp_path):
    np = pytest.importorskip("numpy")
    cache = tmp_path / "semantic-index.json"
    saved = SemanticMemory(_Memory(), model_name="test-model")
    saved._index_built = True
    saved._paths = ["src/example.py"]
    saved._embeddings = np.asarray([[0.25, 0.75]], dtype="float32")

    saved.save_index(str(cache))
    loaded = SemanticMemory(_Memory(), model_name="test-model")

    assert loaded.load_index(str(cache)) is True
    assert loaded._paths == saved._paths
    assert np.array_equal(loaded._embeddings, saved._embeddings)

