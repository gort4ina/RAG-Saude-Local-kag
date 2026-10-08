"""Testes das melhorias incrementais P1–P3."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.embeddings import OllamaEmbeddingProvider
from app.services.entity_aliases import aliases_for
from app.services.frontmatter import parse_frontmatter
from app.services.llm_extractor import parse_llm_relations
from app.services.normalize import normalize_entity_name
from app.services.pgvector_store import cosine_similarity
from app.services.query_router import route_query
from app.services.reranker import NullReranker, parse_rank_order
from app.services.retrieval import Candidate, fuse
from app.services.bm25_index import Bm25Hit
from eval.sweep import build_grid


def test_normalize_entity_collapses_case_and_accents() -> None:
    assert normalize_entity_name("AFE") == normalize_entity_name("afe")
    assert normalize_entity_name("Vigilância") == "vigilancia"


def test_anvisa_has_official_aliases() -> None:
    aliases = aliases_for("ANVISA")
    assert any("agencia nacional" in item for item in aliases)


def test_router_skips_graph_for_literal_rdc() -> None:
    plan = route_query("o que diz a RDC 67/2007 sobre o art. 5?")
    assert plan.use_graph is False
    assert plan.reason == "literal_ref"


def test_router_keeps_graph_for_multi_hop() -> None:
    plan = route_query("quais normas revogam a RDC 67 e tambem a Portaria 344?")
    assert plan.use_graph is True
    assert plan.reason == "multi_hop"


def test_frontmatter_is_stripped_from_body() -> None:
    text = "---\nauthority: ANVISA\nregulation_number: RDC 67/2007\nstatus: vigente_confirmada\n---\n# Corpo\nArt. 1"
    meta, body = parse_frontmatter(text)
    assert meta["authority"] == "ANVISA"
    assert meta["status"] == "vigente_confirmada"
    assert body.startswith("# Corpo")


def test_frontmatter_rejects_invalid_status() -> None:
    meta, _ = parse_frontmatter("---\nstatus: inventado\n---\ntexto")
    assert "status" not in meta


def test_parse_rank_order() -> None:
    assert parse_rank_order("A ordem e [3, 1, 2]", 3) == [3, 1, 2]


@pytest.mark.asyncio
async def test_null_reranker_preserves_order() -> None:
    candidates = [
        Candidate(chunk_id="a", text="a", metadata={}),
        Candidate(chunk_id="b", text="b", metadata={}),
    ]
    ranked = await NullReranker().rerank("q", candidates, limit=1)
    assert [item.chunk_id for item in ranked] == ["a"]


def test_ollama_embedding_provider_delegates() -> None:
    class Fake:
        embedding_model = "embeddinggemma"

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] for _ in texts]

    provider = OllamaEmbeddingProvider(Fake())
    assert provider.model == "embeddinggemma"


def test_llm_relation_parser_keeps_only_allowed() -> None:
    raw = '{"relation":"trata_de","target":"POP","target_kind":"tema","confidence":0.6}\n{"relation":"hack","target":"x"}'
    parsed = parse_llm_relations(raw, source_span="Art. 5")
    assert len(parsed) == 1
    assert parsed[0].extracted_by == "llm"
    assert parsed[0].confidence < 1.0


def test_cosine_similarity_bounds() -> None:
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)


def test_fuse_includes_graph_hits() -> None:
    vector = [
        {
            "id": "a",
            "text": "vetor",
            "score": 0.9,
            "metadata": {"document_id": "d1", "chunk": 1},
        }
    ]
    graph = [
        {
            "id": "g1",
            "text": "do grafo",
            "metadata": {"document_id": "d2", "chunk": 1},
        }
    ]
    fused = fuse(vector, [], {}, question="x", graph_hits=graph)
    assert {item.chunk_id for item in fused} == {"a", "g1"}


def test_fuse_boosts_chunk_also_found_by_graph() -> None:
    vector = [
        {"id": "a", "text": "um", "score": 0.5, "metadata": {"chunk": 1}},
        {"id": "b", "text": "dois", "score": 0.49, "metadata": {"chunk": 2}},
    ]
    graph = [{"id": "b", "text": "dois", "metadata": {"chunk": 2}}]
    fused = fuse(vector, [], {}, question="x", graph_hits=graph)
    assert fused[0].chunk_id == "b"


def test_sweep_grid_skips_overlap_gte_chunk() -> None:
    grid = build_grid(chunk_chars=(100,), overlaps=(20, 100), scores=(0.45,))
    assert len(grid) == 1
    assert grid[0]["collection_name"].startswith("sweep_")


def test_markdown_frontmatter_ingest(tmp_path: Path) -> None:
    from app.services.document_loader import extract_document

    path = tmp_path / "norma.md"
    path.write_text(
        "---\nauthority: ANVISA\nregulation_number: RDC 67/2007\n---\n"
        "Artigo com texto suficiente para passar no minimo de caracteres exigido.",
        encoding="utf-8",
    )
    result = extract_document(path, min_chars_per_page=10)
    assert result.frontmatter["authority"] == "ANVISA"
    assert "Artigo com texto" in result.pages[0].text
