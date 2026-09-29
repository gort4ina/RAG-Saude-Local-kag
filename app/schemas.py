"""Modelos de entrada e saida da API."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# Senhas triviais passam por qualquer regra de composição, então elas são
# barradas por lista. A comparação ignora acentos, caixa e dígitos no fim.
WEAK_PASSWORD_STEMS = frozenset(
    {
        "senha",
        "password",
        "123456",
        "qwerty",
        "admin",
        "administrador",
        "abcdef",
        "iloveyou",
        "welcome",
        "letmein",
        "mudar",
        "trocar",
        "farmacia",
        "anvisa",
        "regulatorio",
    }
)

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 200


def _normalize(value: str) -> str:
    stripped = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in stripped if not unicodedata.combining(char))


def validate_password_strength(password: str, username: str | None = None) -> str:
    """Impõe tamanho, variedade de caracteres e ausência de termos óbvios."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"A senha precisa de pelo menos {MIN_PASSWORD_LENGTH} caracteres."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"A senha não pode passar de {MAX_PASSWORD_LENGTH} caracteres."
        )

    classes = [
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"\d", password)),
        bool(re.search(r"[^A-Za-z0-9]", password)),
    ]
    if not all(classes):
        raise ValueError(
            "A senha precisa combinar letras minúsculas, maiúsculas, "
            "números e ao menos um símbolo."
        )

    normalized = _normalize(password)
    if username and _normalize(username) in normalized:
        raise ValueError("A senha não pode conter o nome de usuário.")

    bare = re.sub(r"[^a-z]", "", normalized)
    if any(stem in bare for stem in WEAK_PASSWORD_STEMS):
        raise ValueError("A senha contém um termo comum demais e é fácil de adivinhar.")
    if len(set(password)) < 6:
        raise ValueError("A senha tem repetição excessiva de caracteres.")
    return password


class ChatRequest(BaseModel):
    """Pergunta enviada ao pipeline RAG."""

    question: str = Field(min_length=3, max_length=2_000)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserProfile(BaseModel):
    user_id: str
    tenant_id: str
    username: str
    role: str
    scopes: list[str]
    session_idle_minutes: int = 20


class UserSummary(BaseModel):
    """Visão administrativa de um usuário do tenant."""

    user_id: str
    username: str
    role: str
    active: bool
    locked: bool = False
    last_login_at: datetime | None = None
    created_at: datetime


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    role: Literal["admin", "user"] = "user"

    @model_validator(mode="after")
    def _check_password(self) -> "CreateUserRequest":
        validate_password_strength(self.password, self.username)
        return self


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )

    @model_validator(mode="after")
    def _check_password(self) -> "ChangePasswordRequest":
        if self.current_password == self.new_password:
            raise ValueError("A nova senha precisa ser diferente da atual.")
        validate_password_strength(self.new_password)
        return self


class UserStatusUpdate(BaseModel):
    active: bool


class LogoutResponse(BaseModel):
    revoked: bool = True


class Source(BaseModel):
    """Trecho recuperado, com rastreabilidade regulatoria completa.

    Campos ``None`` significam "informacao ausente no documento". Nenhum
    metadado e inferido ou inventado.
    """

    document: str
    page: int | None = None
    chunk: int
    score: float
    lexical_score: float = 0.0
    excerpt: str
    authority: str | None = None
    regulation_number: str | None = None
    publication_date: str | None = None
    effective_date: str | None = None
    status: str | None = None
    status_label: str = ""
    article: str | None = None
    section: str | None = None
    source_url: str | None = None
    document_version: str | None = None
    cited: bool = False


class KagRelationView(BaseModel):
    """Aresta do KAG exposta ao frontend/UI.

    O formato espelha ``services.knowledge_graph.KagRelation`` para que o
    payload da API seja um contrato explícito, não um dicionário livre.
    """

    id: str
    relation: str
    subject: str
    subject_kind: str
    object: str
    object_kind: str
    source_reference: str | None = None
    source_document_id: str | None = None
    validation_status: str
    extracted_by: str = "dictionary"
    confidence: float = 1.0
    source_span: str | None = None
    validated_by: str | None = None
    validated_at: datetime | None = None


class KagValidationUpdate(BaseModel):
    """Decisão humana sobre uma aresta KAG."""

    status: Literal["validada", "rejeitada", "pendente_de_validacao"]


class ChatResponse(BaseModel):
    """Resposta final com rastreabilidade, avisos e metricas."""

    answer: str
    sources: list[Source] = Field(default_factory=list)
    retrieved_sources: list[Source] = Field(default_factory=list)
    relations: list[KagRelationView] = Field(default_factory=list)
    requires_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    grounded: bool = True
    disclaimer: str
    request_id: str = ""
    duration_ms: int = 0
    collection_count: int = 0
    embed_ms: int = 0
    retrieval_ms: int = 0
    graph_ms: int = 0
    chat_ms: int = 0
    load_duration_ms: int = 0
    eval_count: int = 0
    tokens_per_second: float = 0.0


class UploadResponse(BaseModel):
    """Resumo do processamento de um documento."""

    document_id: str
    filename: str
    pages: int
    total_pages: int = 0
    chunks: int
    chars_per_page: dict[int, int] = Field(default_factory=dict)
    authority: str | None = None
    regulation_number: str | None = None
    status: str | None = None
    status_label: str = ""


class DocumentSummary(BaseModel):
    """Documento presente na colecao vetorial."""

    document_id: str
    filename: str
    chunks: int
    authority: str | None = None
    regulation_number: str | None = None
    status: str | None = None
    status_label: str = ""
    publication_date: str | None = None


class DocumentStatusUpdate(BaseModel):
    """Confirmacao humana da situacao regulatoria de um documento."""

    status: Literal[
        "vigente_confirmada",
        "revogada_confirmada",
        "vigencia_nao_verificada",
    ]


class DocumentStatusResponse(BaseModel):
    document_id: str
    status: str
    status_label: str
    updated_chunks: int
    validated_by: str | None = None
    validated_at: datetime | None = None


class DeleteResponse(BaseModel):
    document_id: str
    deleted: bool


class AuditEventResponse(BaseModel):
    id: str
    request_id: str
    user_id: str
    action: str
    status: str
    query_text: str | None = None
    retrieved_sources: list[dict[str, Any]] = Field(default_factory=list)
    cited_sources: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0
    ip_address: str | None = None
    created_at: datetime


class HealthResponse(BaseModel):
    """Estado basico dos componentes da aplicacao."""

    status: Literal["ok", "degraded"] = "ok"
    ollama: Literal["online", "offline"] = "offline"
    chat_model: str
    embedding_model: str


class ReadyResponse(BaseModel):
    """Estado de prontidao para receber consultas."""

    ready: bool
    reasons: list[str] = Field(default_factory=list)


class ComponentStatus(BaseModel):
    """Status de um componente monitorado."""

    name: str
    ok: bool
    detail: str = ""


class LoadedModel(BaseModel):
    """Modelo residente em memoria (equivalente ao `ollama ps`)."""

    name: str
    size_mb: int = 0
    size_vram_mb: int = 0
    on_gpu: bool = False


class SystemStatus(BaseModel):
    """Estado consolidado do sistema."""

    app_name: str
    app_env: str
    components: list[ComponentStatus]
    chat_model: str
    embedding_model: str
    chat_model_installed: bool
    embedding_model_installed: bool
    collection_name: str
    documents_count: int
    chunks_count: int
    bm25_index_size: int = 0
    hybrid_search_enabled: bool = True
    loaded_models: list[LoadedModel] = Field(default_factory=list)
    gpu_in_use: bool = False
    gpu_detail: str = ""


class KnowledgeBaseStatus(BaseModel):
    """Estado da base de conhecimento."""

    ready: bool
    collection_name: str
    embedding_model: str = ""
    documents_count: int
    chunks_count: int
    bm25_index_size: int = 0
    unverified_documents: int = 0
    reason: str = ""


class ErrorResponse(BaseModel):
    """Formato uniforme de erros ao cliente."""

    code: str
    detail: str
    request_id: str = ""
