"""Hierarquia de erros de dominio para a RAG.

Cada erro carrega:
- codigo estavel (usado pelo frontend para diferenciar mensagens);
- mensagem segura (mostrada ao usuario final);
- causa original preservada em ``__cause__`` para os logs.

Nenhuma mensagem de usuario deve conter stack trace, caminho de arquivo,
trecho de documento ou conteudo de pergunta.
"""

from __future__ import annotations


class RagError(RuntimeError):
    """Erro base do dominio. Nao deve ser levantado diretamente."""

    code: str = "internal_error"
    http_status: int = 500
    user_message: str = "Ocorreu um erro interno inesperado."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.user_message)


# ---------------------------------------------------------------------------
# Falhas do servico de modelos (Ollama)
# ---------------------------------------------------------------------------


class OllamaUnavailableError(RagError):
    """Base para qualquer falha do Ollama."""

    code = "ollama_unavailable"
    http_status = 503
    user_message = (
        "O servico de modelos (Ollama) esta indisponivel. "
        "Verifique se o container 'ollama' esta rodando."
    )


class OllamaConnectionError(OllamaUnavailableError):
    """Nao foi possivel abrir a conexao TCP com o Ollama."""

    code = "ollama_connection_error"
    user_message = (
        "Nao foi possivel conectar ao servico de modelos. "
        "Confirme que o container 'ollama' esta no ar e acessivel na rede do compose."
    )


class OllamaTimeoutError(OllamaUnavailableError):
    """Ollama demorou mais que o timeout configurado."""

    code = "ollama_timeout"
    user_message = (
        "O modelo demorou mais que o tempo limite para responder. "
        "Isso costuma ocorrer quando a inferencia esta em CPU. "
        "Considere habilitar a GPU ou usar um modelo menor."
    )


class OllamaHttpError(OllamaUnavailableError):
    """Ollama respondeu com um status HTTP invalido."""

    code = "ollama_http_error"

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"Ollama respondeu HTTP {status_code}. {detail}".strip())
        self.user_message = (
            f"O servico de modelos respondeu com erro HTTP {status_code}. "
            "Consulte os logs do container 'ollama'."
        )


class OllamaOverloadedError(OllamaUnavailableError):
    """Ollama recusou a requisicao por saturacao de fila ou memoria."""

    code = "ollama_overloaded"
    user_message = (
        "O servico de modelos esta sobrecarregado ou sem memoria disponivel. "
        "Aguarde a requisicao anterior terminar ou reduza o tamanho do contexto."
    )


class OllamaEmptyResponseError(OllamaUnavailableError):
    """O modelo terminou a geracao sem produzir nenhum token."""

    code = "ollama_empty_response"
    user_message = (
        "O modelo retornou uma resposta vazia. "
        "Tente reformular a pergunta ou reduzir o contexto enviado."
    )


class ModelNotInstalledError(OllamaUnavailableError):
    """O modelo requisitado nao esta baixado no Ollama."""

    code = "model_not_installed"

    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(
            f"O modelo '{model}' nao foi encontrado no Ollama. "
            f"Baixe-o com 'ollama pull {model}'."
        )
        self.user_message = str(self)


class StreamInterruptedError(OllamaUnavailableError):
    """A conexao de streaming caiu no meio da geracao."""

    code = "stream_interrupted"
    user_message = (
        "A conexao com o modelo foi interrompida durante a geracao da resposta. "
        "Tente novamente."
    )


# ---------------------------------------------------------------------------
# Falhas do banco vetorial
# ---------------------------------------------------------------------------


class VectorStoreUnavailableError(RagError):
    """Banco vetorial (ChromaDB) inacessivel."""

    code = "vector_store_unavailable"
    http_status = 503
    user_message = (
        "O banco vetorial esta indisponivel. "
        "Verifique o volume persistente e reinicie o servico da API."
    )


class AuditUnavailableError(RagError):
    """Banco de auditoria indisponível; operações rastreáveis falham fechadas."""

    code = "audit_unavailable"
    http_status = 503
    user_message = (
        "A trilha de auditoria está indisponível. "
        "A operação foi bloqueada para preservar a rastreabilidade."
    )


class EmbeddingModelMismatchError(RagError):
    """A colecao foi criada com outro modelo de embeddings.

    Vetores de modelos distintos tem dimensoes e geometrias diferentes.
    Misturar os dois na mesma colecao produz recuperacao silenciosamente
    errada, entao a operacao e bloqueada.
    """

    code = "embedding_model_mismatch"
    http_status = 409

    def __init__(self, collection: str, expected: str, configured: str) -> None:
        self.collection = collection
        self.expected = expected
        self.configured = configured
        super().__init__(
            f"A colecao '{collection}' foi indexada com o modelo de embeddings "
            f"'{expected}', mas a configuracao atual usa '{configured}'. "
            "Use uma colecao nova (COLLECTION_NAME) ou volte o modelo anterior. "
            "A colecao existente nao foi alterada."
        )
        self.user_message = str(self)


# ---------------------------------------------------------------------------
# Falhas de dominio
# ---------------------------------------------------------------------------


class KnowledgeBaseEmptyError(RagError):
    """Nenhum documento foi indexado ainda."""

    code = "knowledge_base_empty"
    http_status = 409
    user_message = (
        "Ainda nao ha documentos indexados na base. "
        "Adicione um documento antes de fazer perguntas."
    )


class NoRelevantContextError(RagError):
    """Nenhum trecho passou do limite minimo de relevancia."""

    code = "no_relevant_context"
    http_status = 200  # a rota devolve resposta explicita, nao erro HTTP
    user_message = (
        "Nao encontrei informacao suficiente na base documental para "
        "responder com seguranca."
    )


class UnsupportedDocumentError(RagError):
    """Formato de documento nao suportado."""

    code = "unsupported_document"
    http_status = 415
    user_message = "Formato de arquivo nao suportado. Use PDF, TXT ou Markdown."


class DocumentTooLargeError(RagError):
    """Arquivo maior que o limite configurado."""

    code = "document_too_large"
    http_status = 413

    def __init__(self, max_mb: int) -> None:
        super().__init__(f"O arquivo ultrapassa {max_mb} MB.")
        self.user_message = str(self)


class EmptyDocumentError(RagError):
    """Arquivo enviado sem conteudo util."""

    code = "empty_document"
    http_status = 400
    user_message = "O arquivo esta vazio ou nao possui texto extraivel."


class ScannedPdfError(RagError):
    """PDF sem camada de texto: precisa de OCR."""

    code = "scanned_pdf_requires_ocr"
    http_status = 422

    def __init__(self, pages_with_text: int, total_pages: int) -> None:
        self.pages_with_text = pages_with_text
        self.total_pages = total_pages
        super().__init__(
            f"Apenas {pages_with_text} de {total_pages} pagina(s) possuem texto "
            "extraivel. Este PDF parece ser digitalizado (imagem). "
            "Aplique OCR antes de enviar - o projeto nao executa OCR."
        )
        self.user_message = str(self)


class IngestionInProgressError(RagError):
    """Ja existe uma indexacao em andamento para o mesmo documento."""

    code = "ingestion_in_progress"
    http_status = 409
    user_message = (
        "Este documento ja esta sendo indexado neste momento. "
        "Aguarde a conclusao antes de enviar novamente."
    )


class IndexingFailedError(RagError):
    """A indexacao falhou; o indice anterior foi preservado."""

    code = "indexing_failed"
    http_status = 500
    user_message = (
        "Nao foi possivel indexar o documento. "
        "A versao anterior da base foi preservada e continua consultavel."
    )


class InvalidQuestionError(RagError):
    """Pergunta invalida (curta demais, vazia, etc.)."""

    code = "invalid_question"
    http_status = 422
    user_message = "A pergunta precisa ter entre 3 e 2000 caracteres."
