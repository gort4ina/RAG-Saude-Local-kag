"""Construcao compartilhada dos servicos."""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.database import get_session_factory
from app.services.bm25_index import Bm25Index
from app.services.graph_store import GraphStore
from app.services.knowledge_graph import PostgresGraphStore
from app.services.ollama_client import OllamaClient
from app.services.rag_service import RagOptions, RagService
from app.services.vector_store import VectorStore


@lru_cache(maxsize=1)
def get_knowledge_graph_service() -> GraphStore | None:
    """Monta o GraphStore conforme ``KAG_ENABLED`` / ``KAG_GRAPH_BACKEND``.

    Retorna ``None`` quando o KAG está desligado: o ``RagService`` opera
    então como RAG puro (vetor + BM25), sem tocar no grafo.
    """
    settings = get_settings()
    if not settings.kag_enabled:
        return None

    backend = settings.kag_graph_backend
    if backend == "postgres":
        return PostgresGraphStore(get_session_factory())

    if backend == "neptune":
        # Import lazy: o stub não puxa SDK AWS e só falha se for ativado.
        from app.services.neptune_graph_store import NeptuneGraphStore

        return NeptuneGraphStore(
            endpoint=settings.neptune_endpoint,
            port=settings.neptune_port,
            use_iam=settings.neptune_use_iam,
            region=settings.neptune_region,
        )

    raise ValueError(f"KAG_GRAPH_BACKEND desconhecido: {backend}")


@lru_cache(maxsize=1)
def get_rag_service() -> RagService:
    """Monta e memoriza o pipeline RAG.

    A instancia e unica por processo: o pool HTTP do Ollama, a conexao do
    ChromaDB e o indice BM25 sao reaproveitados entre requisicoes.
    """
    settings = get_settings()
    settings.ensure_directories()

    ollama = OllamaClient(
        settings.ollama_base_url,
        settings.chat_model,
        settings.embedding_model,
        health_timeout=settings.ollama_health_timeout,
        embed_timeout=settings.ollama_embed_timeout,
        chat_timeout=settings.ollama_chat_timeout,
        num_ctx=settings.ollama_context_length,
        num_predict=settings.ollama_num_predict,
        temperature=settings.ollama_temperature,
    )
    if settings.vector_backend == "pgvector":
        from app.services.pgvector_store import PgVectorStore

        store = PgVectorStore(
            get_session_factory(),
            embedding_model=settings.embedding_model,
            collection_name=settings.collection_name,
        )
    else:
        store = VectorStore(
            settings.chroma_path,
            settings.collection_name,
            embedding_model=settings.embedding_model,
        )
    from app.services.embeddings import OllamaEmbeddingProvider
    from app.services.ocr import TesseractOcrBackend
    from app.services.reranker import OllamaPromptReranker

    embeddings = OllamaEmbeddingProvider(ollama)
    reranker = OllamaPromptReranker(ollama) if settings.reranker_enabled else None
    ocr = TesseractOcrBackend() if settings.ocr_enabled else None
    graph = get_knowledge_graph_service()
    if graph is not None and settings.kag_llm_extractor:
        from app.services.llm_extractor import LLMAssistedRelationExtractor

        graph.llm_extractor = LLMAssistedRelationExtractor(ollama)  # type: ignore[attr-defined]
    return RagService(
        ollama=ollama,
        store=store,
        options=RagOptions(
            retrieval_candidates=settings.retrieval_candidates,
            max_context_chunks=settings.max_context_chunks,
            min_relevance_score=settings.min_relevance_score,
            chunk_chars=settings.chunk_chars,
            chunk_overlap_chars=settings.chunk_overlap_chars,
            embed_batch_size=settings.embed_batch_size,
            hybrid_search_enabled=settings.hybrid_search_enabled,
            rrf_k=settings.rrf_k,
            dedup_similarity=settings.dedup_similarity,
            min_chars_per_page=settings.min_chars_per_page,
            min_extraction_ratio=settings.min_extraction_ratio,
            graph_in_rrf=settings.graph_in_rrf,
            reranker_enabled=settings.reranker_enabled,
            reranker_candidates=settings.reranker_candidates,
            query_router_enabled=settings.query_router_enabled,
        ),
        bm25=Bm25Index(),
        upload_path=settings.upload_path,
        knowledge_graph=graph,  # type: ignore[arg-type]
        embeddings=embeddings,
        reranker=reranker,
        ocr=ocr,
    )
