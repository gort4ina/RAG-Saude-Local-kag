"""Testes do VectorStore com ChromaDB real em diretorio temporario."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import EmbeddingModelMismatchError
from app.services.vector_store import VectorStore

TENANT = "tenant-a"


def _store(path: Path, model: str = "embeddinggemma", name: str = "colecao_teste"):
    return VectorStore(path, name, embedding_model=model)


def test_new_collection_is_stamped_with_embedding_model(tmp_path: Path) -> None:
    store = _store(tmp_path / "chroma")
    assert store.collection.metadata.get("embedding_model") == "embeddinggemma"


def test_reopening_with_same_model_works(tmp_path: Path) -> None:
    path = tmp_path / "chroma"
    _store(path).upsert(TENANT, ["a:1"], ["texto"], [[0.1, 0.2]], [{"tenant_id": TENANT, "document_id": "a", "filename": "a.md"}])
    reopened = _store(path)
    assert reopened.count_chunks(TENANT) == 1


def test_changing_embedding_model_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "chroma"
    _store(path, model="embeddinggemma")

    with pytest.raises(EmbeddingModelMismatchError) as info:
        _store(path, model="bge-m3")

    assert "embeddinggemma" in info.value.user_message
    assert "bge-m3" in info.value.user_message


def test_previous_collection_is_preserved_when_switching_names(tmp_path: Path) -> None:
    """Trocar COLLECTION_NAME nao apaga a colecao anterior."""
    path = tmp_path / "chroma"
    old = VectorStore(path, "colecao_antiga", embedding_model="bge-m3")
    old.upsert(TENANT, ["a:1"], ["texto antigo"], [[0.1, 0.2]], [{"tenant_id": TENANT, "document_id": "a", "filename": "a.md"}])

    new = VectorStore(path, "colecao_nova", embedding_model="embeddinggemma")
    new.upsert(TENANT, ["b:1"], ["texto novo"], [[0.3, 0.4]], [{"tenant_id": TENANT, "document_id": "b", "filename": "b.md"}])

    reopened_old = VectorStore(path, "colecao_antiga", embedding_model="bge-m3")
    assert reopened_old.count_chunks(TENANT) == 1
    assert new.count_chunks(TENANT) == 1


def test_query_returns_ids_and_scores(tmp_path: Path) -> None:
    store = _store(tmp_path / "chroma")
    store.upsert(
        TENANT,
        ["a:1", "a:2"],
        ["primeiro trecho", "segundo trecho"],
        [[1.0, 0.0], [0.0, 1.0]],
        [
            {"tenant_id": TENANT, "document_id": "a", "filename": "a.md", "chunk": 1},
            {"tenant_id": TENANT, "document_id": "a", "filename": "a.md", "chunk": 2},
        ],
    )
    results = store.query(TENANT, [1.0, 0.0], limit=2)
    assert results[0]["id"] == "a:1"
    assert 0.0 <= results[0]["score"] <= 1.0
    assert results[0]["score"] > results[1]["score"]


def test_all_chunks_feeds_bm25(tmp_path: Path) -> None:
    store = _store(tmp_path / "chroma")
    store.upsert(TENANT, ["a:1"], ["RDC 67/2007"], [[0.1, 0.2]], [{"tenant_id": TENANT, "document_id": "a", "filename": "a.md"}])
    assert store.all_chunks(TENANT) == [("a:1", "RDC 67/2007")]


def test_get_chunks_by_id(tmp_path: Path) -> None:
    store = _store(tmp_path / "chroma")
    store.upsert(TENANT, ["a:1"], ["texto"], [[0.1, 0.2]], [{"tenant_id": TENANT, "document_id": "a", "filename": "a.md"}])
    found = store.get_chunks(TENANT, ["a:1", "inexistente"])
    assert set(found) == {"a:1"}
    assert found["a:1"]["text"] == "texto"


def test_delete_ids_removes_only_the_listed_chunks(tmp_path: Path) -> None:
    store = _store(tmp_path / "chroma")
    store.upsert(
        TENANT,
        ["a:1", "a:2"],
        ["um", "dois"],
        [[0.1, 0.2], [0.3, 0.4]],
        [
            {"tenant_id": TENANT, "document_id": "a", "filename": "a.md"},
            {"tenant_id": TENANT, "document_id": "a", "filename": "a.md"},
        ],
    )
    store.delete_ids(TENANT, ["a:2"])
    assert store.count_chunks(TENANT) == 1
    assert store.chunk_ids_for_document(TENANT, "a") == ["a:1"]


def test_list_documents_tolerates_missing_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path / "chroma")
    store.upsert(TENANT, ["a:1"], ["texto"], [[0.1, 0.2]], [{"tenant_id": TENANT, "document_id": "a"}])
    documents = store.list_documents(TENANT)
    assert documents[0]["filename"] == "documento"


def test_tenant_filter_prevents_cross_organization_leak(tmp_path: Path) -> None:
    store = _store(tmp_path / "chroma")
    store.upsert(
        "tenant-a",
        ["tenant-a:doc:1"],
        ["segredo da organizacao A"],
        [[1.0, 0.0]],
        [{"tenant_id": "tenant-a", "document_id": "doc", "filename": "a.md"}],
    )
    store.upsert(
        "tenant-b",
        ["tenant-b:doc:1"],
        ["conteudo da organizacao B"],
        [[1.0, 0.0]],
        [{"tenant_id": "tenant-b", "document_id": "doc", "filename": "b.md"}],
    )

    assert [item["id"] for item in store.query("tenant-a", [1.0, 0.0], 5)] == [
        "tenant-a:doc:1"
    ]
    assert [item["filename"] for item in store.list_documents("tenant-b")] == [
        "b.md"
    ]
    store.delete_document("tenant-a", "doc")
    assert store.count_chunks("tenant-a") == 0
    assert store.count_chunks("tenant-b") == 1
