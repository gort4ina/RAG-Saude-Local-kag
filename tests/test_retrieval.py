"""Testes de BM25, fusao RRF, deduplicacao e selecao de contexto."""

from __future__ import annotations

from app.services.bm25_index import Bm25Index, reference_tokens, tokenize
from app.services.retrieval import Candidate, deduplicate, fuse, select_context


# ---------------------------------------------------------------------------
# Tokenizacao
# ---------------------------------------------------------------------------


def test_tokenize_expands_regulation_identifiers() -> None:
    tokens = set(tokenize("RDC 67/2007"))
    assert "rdc" in tokens
    assert "67/2007" in tokens
    assert "67/07" in tokens


def test_tokenize_normalizes_two_digit_year() -> None:
    tokens = set(tokenize("Portaria 344/98"))
    assert "344/98" in tokens
    assert "344/1998" in tokens
    assert "1998" in tokens


def test_tokenize_captures_article_reference() -> None:
    assert "art5" in set(tokenize("o que diz o art. 5 da norma"))
    assert "art5" in set(tokenize("Artigo 5o"))


def test_tokenize_strips_accents_and_stopwords() -> None:
    tokens = tokenize("a inspeção de vigilância sanitária")
    assert "inspecao" in tokens
    assert "vigilancia" in tokens
    assert "a" not in tokens


def test_reference_tokens_only_returns_identifiers() -> None:
    assert reference_tokens("RDC 67/2007 art. 5") == {"67/2007", "67/07", "art5"}
    assert reference_tokens("quais os requisitos gerais") == set()


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------


def _index() -> Bm25Index:
    index = Bm25Index()
    index.rebuild(
        [
            ("d1:1", "Norma: RDC 67/2007. Art. 5 trata das boas praticas de manipulacao."),
            ("d1:2", "Norma: RDC 67/2007. Art. 6 trata da documentacao da qualidade."),
            ("d2:1", "Norma: Portaria 344/98. Lista A1 de substancias entorpecentes."),
            ("d3:1", "A AFE e a Autorizacao de Funcionamento de Empresa emitida pela Anvisa."),
            ("d4:1", "O SNGPC recebe a escrituracao de produtos sob controle especial."),
        ]
    )
    return index


def test_bm25_finds_rdc_67_2007() -> None:
    hits = _index().search("RDC 67/2007", limit=3)
    assert hits
    assert hits[0].chunk_id.startswith("d1:")


def test_bm25_matches_portaria_with_two_digit_year() -> None:
    hits = _index().search("Portaria 344/98", limit=3)
    assert hits[0].chunk_id == "d2:1"


def test_bm25_finds_acronyms() -> None:
    assert _index().search("AFE", limit=1)[0].chunk_id == "d3:1"
    assert _index().search("SNGPC", limit=1)[0].chunk_id == "d4:1"


def test_bm25_finds_article_number() -> None:
    hits = _index().search("art. 6", limit=2)
    assert hits[0].chunk_id == "d1:2"


def test_bm25_empty_index_returns_nothing() -> None:
    index = Bm25Index()
    assert index.search("qualquer coisa", limit=5) == []
    assert index.size == 0


def test_bm25_rebuild_replaces_previous_content() -> None:
    index = _index()
    assert index.size == 5
    index.rebuild([("novo:1", "conteudo totalmente diferente")])
    assert index.size == 1
    assert index.search("RDC 67/2007", limit=5) == []


# ---------------------------------------------------------------------------
# Fusao RRF
# ---------------------------------------------------------------------------


def _vector(chunk_id: str, score: float, text: str = "texto", **metadata):
    return {
        "id": chunk_id,
        "text": text,
        "score": score,
        "metadata": {"document_id": "d1", "chunk": 1, "filename": "a.pdf", **metadata},
    }


def test_rrf_promotes_chunk_present_in_both_rankings() -> None:
    from app.services.bm25_index import Bm25Hit

    vector_results = [
        _vector("a", 0.80, "texto a"),
        _vector("b", 0.78, "texto b"),
    ]
    bm25_hits = [Bm25Hit("b", 9.0), Bm25Hit("c", 4.0)]
    lexical = {"c": {"text": "texto c", "metadata": {"document_id": "d3", "chunk": 1}}}

    fused = fuse(vector_results, bm25_hits, lexical, question="pergunta", rrf_k=60)

    # "b" aparece nas duas listas e por isso ultrapassa "a", que so aparece numa.
    assert fused[0].chunk_id == "b"
    assert {candidate.chunk_id for candidate in fused} == {"a", "b", "c"}


def test_fuse_ignores_bm25_hit_without_stored_document() -> None:
    from app.services.bm25_index import Bm25Hit

    fused = fuse([], [Bm25Hit("fantasma", 5.0)], {}, question="x")
    assert fused == []


def test_fuse_marks_exact_reference_match() -> None:
    from app.services.bm25_index import Bm25Hit

    vector_results = [_vector("a", 0.20, "Norma: RDC 67/2007. Art. 5 boas praticas.")]
    fused = fuse(
        vector_results, [Bm25Hit("a", 8.0)], {}, question="o que diz a RDC 67/2007?"
    )
    assert fused[0].exact_reference is True
    assert fused[0].lexical_score == 1.0


# ---------------------------------------------------------------------------
# Deduplicacao e selecao
# ---------------------------------------------------------------------------


def _candidate(chunk_id: str, text: str, score: float, chunk: int, document="d1"):
    candidate = Candidate(
        chunk_id=chunk_id,
        text=text,
        metadata={"document_id": document, "chunk": chunk},
        vector_score=score,
        rrf_score=score,
    )
    candidate.tokens = frozenset(tokenize(text))
    return candidate


def test_deduplicate_removes_near_identical_chunks() -> None:
    text = "A farmacia deve manter registro de todas as formulas manipuladas diariamente."
    candidates = [
        _candidate("a", text, 0.9, 1),
        _candidate("b", text, 0.8, 5),
    ]
    assert len(deduplicate(candidates, similarity_threshold=0.90)) == 1


def test_deduplicate_reduces_overlapping_adjacent_chunks() -> None:
    base = "registro de formulas manipuladas com controle de qualidade documentado"
    candidates = [
        _candidate("d1:4", base + " parte um", 0.9, 4),
        _candidate("d1:5", base + " parte dois", 0.8, 5),
    ]
    assert len(deduplicate(candidates, similarity_threshold=0.95)) == 1


def test_deduplicate_keeps_distinct_chunks() -> None:
    candidates = [
        _candidate("a", "boas praticas de manipulacao de formulas", 0.9, 1),
        _candidate("b", "escrituracao no SNGPC de produtos controlados", 0.8, 9, "d2"),
    ]
    assert len(deduplicate(candidates)) == 2


def test_select_context_applies_threshold_and_limit() -> None:
    candidates = [
        _candidate("a", "texto relevante um sobre manipulacao", 0.80, 1),
        _candidate("b", "texto relevante dois sobre inspecao", 0.60, 2, "d2"),
        _candidate("c", "texto irrelevante tres sobre outra coisa", 0.10, 3, "d3"),
    ]
    selected = select_context(
        candidates, min_relevance_score=0.45, max_context_chunks=4
    )
    assert [item.chunk_id for item in selected] == ["a", "b"]

    limited = select_context(
        candidates, min_relevance_score=0.45, max_context_chunks=1
    )
    assert len(limited) == 1


def test_select_context_keeps_exact_reference_below_threshold() -> None:
    candidate = _candidate("a", "Norma: RDC 67/2007. Art. 5 boas praticas.", 0.20, 1)
    candidate.exact_reference = True
    candidate.lexical_score = 1.0
    selected = select_context(
        [candidate], min_relevance_score=0.45, max_context_chunks=4
    )
    assert len(selected) == 1


def test_select_context_drops_weak_lexical_match() -> None:
    candidate = _candidate("a", "texto qualquer", 0.20, 1)
    candidate.exact_reference = True
    candidate.lexical_score = 0.1
    assert select_context([candidate], min_relevance_score=0.45, max_context_chunks=4) == []
