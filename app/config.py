"""Configuracao da aplicacao carregada por variaveis de ambiente.

Unidades importantes:

- ``chunk_chars`` e ``chunk_overlap_chars`` sao contados em CARACTERES,
  nao em tokens. Nao ha tokenizador real no pipeline. A conversao usada
  como referencia e ``~3.8 caracteres por token`` para portugues, entao
  ``1900 caracteres ~= 500 tokens`` e ``285 caracteres ~= 75 tokens``.
  Esse valor e uma aproximacao e deve ser recalibrado se um tokenizador
  for adicionado.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Referencia usada apenas para documentar a equivalencia aproximada
# entre a unidade real (caracteres) e a unidade pedida (tokens).
CHARS_PER_TOKEN_ESTIMATE = 3.8


def _csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def _int_env(name: str, default: str) -> int:
    return int(os.getenv(name, default))


def _float_env(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _bool_env(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Valores de configuracao do servico."""

    app_name: str = field(
        default_factory=lambda: os.getenv("APP_NAME", "RAG Regulatorio Farmaceutico")
    )
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # ---------- Identidade e banco relacional ----------
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///./data/rag.db"
        )
    )
    jwt_secret: str = field(
        default_factory=lambda: os.getenv(
            "JWT_SECRET", "development-only-change-this-secret"
        )
    )
    jwt_algorithm: str = field(
        default_factory=lambda: os.getenv("JWT_ALGORITHM", "HS256")
    )
    jwt_issuer: str = field(
        default_factory=lambda: os.getenv("JWT_ISSUER", "rag-regulatorio")
    )
    jwt_audience: str = field(
        default_factory=lambda: os.getenv("JWT_AUDIENCE", "rag-regulatorio-web")
    )
    jwt_access_token_minutes: int = field(
        default_factory=lambda: _int_env("JWT_ACCESS_TOKEN_MINUTES", "15")
    )
    refresh_token_days: int = field(
        default_factory=lambda: _int_env("REFRESH_TOKEN_DAYS", "7")
    )
    refresh_cookie_name: str = field(
        default_factory=lambda: os.getenv("REFRESH_COOKIE_NAME", "rag_refresh")
    )
    csrf_cookie_name: str = field(
        default_factory=lambda: os.getenv("CSRF_COOKIE_NAME", "rag_csrf")
    )
    csrf_header_name: str = field(
        default_factory=lambda: os.getenv("CSRF_HEADER_NAME", "X-CSRF-Token")
    )
    # Só desligue em desenvolvimento sem TLS: sem Secure o cookie de refresh
    # trafega em claro e pode ser capturado na rede.
    cookie_secure: bool = field(
        default_factory=lambda: _bool_env("COOKIE_SECURE", "false")
    )
    login_max_failed_attempts: int = field(
        default_factory=lambda: _int_env("LOGIN_MAX_FAILED_ATTEMPTS", "5")
    )
    login_lockout_minutes: int = field(
        default_factory=lambda: _int_env("LOGIN_LOCKOUT_MINUTES", "15")
    )
    bootstrap_tenant_slug: str = field(
        default_factory=lambda: os.getenv("BOOTSTRAP_TENANT_SLUG", "local")
    )
    bootstrap_tenant_name: str = field(
        default_factory=lambda: os.getenv("BOOTSTRAP_TENANT_NAME", "Organização local")
    )
    bootstrap_admin_username: str = field(
        default_factory=lambda: os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    )
    bootstrap_admin_password: str = field(
        # Sem default. Se nao vier do ambiente o bootstrap simplesmente
        # nao cria admin (bootstrap_admin() ja aborta nesse caso). Isso
        # evita o antipattern historico de materializar admin com senha
        # trivial ('123456') quando a variavel esquece de ser definida.
        default_factory=lambda: os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    )

    # ---------- Ollama ----------
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    chat_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:3b")
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_EMBEDDING_MODEL", "embeddinggemma")
    )
    ollama_health_timeout: float = field(
        default_factory=lambda: _float_env("OLLAMA_HEALTH_TIMEOUT", "5")
    )
    ollama_embed_timeout: float = field(
        default_factory=lambda: _float_env("OLLAMA_EMBED_TIMEOUT", "120")
    )
    ollama_chat_timeout: float = field(
        default_factory=lambda: _float_env("OLLAMA_CHAT_TIMEOUT", "300")
    )
    ollama_context_length: int = field(
        default_factory=lambda: _int_env("OLLAMA_CONTEXT_LENGTH", "4096")
    )
    ollama_num_predict: int = field(
        default_factory=lambda: _int_env("OLLAMA_NUM_PREDICT", "450")
    )
    ollama_temperature: float = field(
        default_factory=lambda: _float_env("OLLAMA_TEMPERATURE", "0.1")
    )

    # ---------- Persistencia ----------
    chroma_path: Path = field(
        default_factory=lambda: Path(os.getenv("CHROMA_PATH", "./data/chroma"))
    )
    upload_path: Path = field(
        default_factory=lambda: Path(os.getenv("UPLOAD_PATH", "./data/uploads"))
    )
    collection_name: str = field(
        default_factory=lambda: os.getenv(
            "COLLECTION_NAME", "assuntos_regulatorios_embeddinggemma_v1"
        )
    )

    # ---------- Recuperacao ----------
    retrieval_candidates: int = field(
        default_factory=lambda: _int_env("RETRIEVAL_CANDIDATES", "12")
    )
    max_context_chunks: int = field(
        default_factory=lambda: _int_env("MAX_CONTEXT_CHUNKS", "4")
    )
    min_relevance_score: float = field(
        default_factory=lambda: _float_env("MIN_RELEVANCE_SCORE", "0.45")
    )
    hybrid_search_enabled: bool = field(
        default_factory=lambda: _bool_env("HYBRID_SEARCH_ENABLED", "true")
    )
    rrf_k: int = field(default_factory=lambda: _int_env("RRF_K", "60"))
    dedup_similarity: float = field(
        default_factory=lambda: _float_env("DEDUP_SIMILARITY", "0.90")
    )

    # ---------- Chunking (unidade: CARACTERES) ----------
    chunk_chars: int = field(default_factory=lambda: _int_env("CHUNK_CHARS", "1900"))
    chunk_overlap_chars: int = field(
        default_factory=lambda: _int_env("CHUNK_OVERLAP_CHARS", "285")
    )
    embed_batch_size: int = field(
        default_factory=lambda: _int_env("EMBED_BATCH_SIZE", "16")
    )
    min_chars_per_page: int = field(
        default_factory=lambda: _int_env("MIN_CHARS_PER_PAGE", "40")
    )
    min_extraction_ratio: float = field(
        default_factory=lambda: _float_env("MIN_EXTRACTION_RATIO", "0.20")
    )

    # ---------- HTTP ----------
    max_upload_mb: int = field(default_factory=lambda: _int_env("MAX_UPLOAD_MB", "15"))
    allowed_origins: list[str] = field(
        default_factory=lambda: _csv_env(
            "ALLOWED_ORIGINS", "http://localhost:4200,http://localhost:8080"
        )
    )
    chat_rate_limit: str = field(
        default_factory=lambda: os.getenv("CHAT_RATE_LIMIT", "10/minute")
    )
    upload_rate_limit: str = field(
        default_factory=lambda: os.getenv("UPLOAD_RATE_LIMIT", "5/hour")
    )
    login_rate_limit: str = field(
        default_factory=lambda: os.getenv("LOGIN_RATE_LIMIT", "5/minute")
    )
    default_rate_limit: str = field(
        default_factory=lambda: os.getenv("DEFAULT_RATE_LIMIT", "120/minute")
    )
    refresh_rate_limit: str = field(
        default_factory=lambda: os.getenv("REFRESH_RATE_LIMIT", "30/minute")
    )
    admin_rate_limit: str = field(
        default_factory=lambda: os.getenv("ADMIN_RATE_LIMIT", "20/minute")
    )
    # ``memory://`` só protege o processo atual. Com mais de um worker use um
    # backend compartilhado (ex.: ``redis://redis:6379/0``).
    rate_limit_storage_uri: str = field(
        default_factory=lambda: os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
    )
    allowed_hosts: list[str] = field(
        default_factory=lambda: _csv_env("ALLOWED_HOSTS", "*")
    )
    # Redes das quais X-Real-IP / X-Forwarded-For são aceitos. Fora delas o
    # cabeçalho é ignorado, senão qualquer cliente burlaria o rate limit por IP.
    trusted_proxy_cidrs: list[str] = field(
        default_factory=lambda: _csv_env(
            "TRUSTED_PROXY_CIDRS", "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
        )
    )
    max_json_body_kb: int = field(
        default_factory=lambda: _int_env("MAX_JSON_BODY_KB", "256")
    )
    # Vazio = segue o ambiente: documentação aberta fora de produção.
    enable_api_docs_raw: str = field(
        default_factory=lambda: os.getenv("ENABLE_API_DOCS", "")
    )
    session_idle_minutes: int = field(
        default_factory=lambda: _int_env("SESSION_IDLE_MINUTES", "20")
    )
    inference_max_concurrency: int = field(
        default_factory=lambda: _int_env("INFERENCE_MAX_CONCURRENCY", "1")
    )
    inference_queue_wait_seconds: float = field(
        default_factory=lambda: _float_env("INFERENCE_QUEUE_WAIT_SECONDS", "0.10")
    )

    # ---------- LGPD / auditoria ----------
    # Retenção dos AuditEvent (dias). O padrão de 2 anos cobre o prazo
    # regulatório típico de investigações internas sem virar dark data.
    # Zero desliga a poda (não recomendado em produção).
    audit_retention_days: int = field(
        default_factory=lambda: _int_env("AUDIT_RETENTION_DAYS", "730")
    )
    audit_prune_interval_hours: int = field(
        default_factory=lambda: _int_env("AUDIT_PRUNE_INTERVAL_HOURS", "24")
    )
    # Persistir a pergunta literal facilita auditoria mas cria PII linkable
    # ao usuário. Padrão: guardar apenas SHA-256 + comprimento; a
    # organização opta explicitamente por texto se precisar.
    audit_persist_query_text: bool = field(
        default_factory=lambda: _bool_env("AUDIT_PERSIST_QUERY_TEXT", "false")
    )

    # ---------- Cota de upload por tenant ----------
    # Impede um tenant de consumir todo o disco. Zero desliga a checagem.
    tenant_upload_quota_mb: int = field(
        default_factory=lambda: _int_env("TENANT_UPLOAD_QUOTA_MB", "2048")
    )

    # ---------- KAG (grafo de conhecimento) ----------
    # Desligar volta o pipeline ao RAG puro (vetor + BM25), sem tocar no grafo.
    # chroma (default) ou pgvector. pgvector exige a tabela knowledge_chunks.
    vector_backend: str = field(
        default_factory=lambda: os.getenv("VECTOR_BACKEND", "chroma").strip().lower()
    )
    embedding_dimension: int = field(
        default_factory=lambda: _int_env("EMBEDDING_DIMENSION", "768")
    )
    reranker_enabled: bool = field(
        default_factory=lambda: _bool_env("RERANKER_ENABLED", "false")
    )
    reranker_candidates: int = field(
        default_factory=lambda: _int_env("RERANKER_CANDIDATES", "20")
    )
    graph_in_rrf: bool = field(
        default_factory=lambda: _bool_env("GRAPH_IN_RRF", "true")
    )
    query_router_enabled: bool = field(
        default_factory=lambda: _bool_env("QUERY_ROUTER_ENABLED", "true")
    )
    kag_llm_extractor: bool = field(
        default_factory=lambda: _bool_env("KAG_LLM_EXTRACTOR", "false")
    )
    ocr_enabled: bool = field(
        default_factory=lambda: _bool_env("OCR_ENABLED", "false")
    )

    kag_enabled: bool = field(
        default_factory=lambda: _bool_env("KAG_ENABLED", "true")
    )
    # ``postgres`` = desenvolvimento local (SQLAlchemy). ``neptune`` = stub
    # AWS Neptune — exige NEPTUNE_ENDPOINT e ainda não executa openCypher.
    kag_graph_backend: str = field(
        default_factory=lambda: os.getenv("KAG_GRAPH_BACKEND", "postgres").strip().lower()
    )
    neptune_endpoint: str = field(
        default_factory=lambda: os.getenv("NEPTUNE_ENDPOINT", "").strip()
    )
    neptune_port: int = field(
        default_factory=lambda: _int_env("NEPTUNE_PORT", "8182")
    )
    neptune_region: str = field(
        default_factory=lambda: os.getenv("NEPTUNE_REGION", "").strip()
    )
    neptune_use_iam: bool = field(
        default_factory=lambda: _bool_env("NEPTUNE_USE_IAM", "true")
    )

    def __post_init__(self) -> None:
        if self.vector_backend not in {"chroma", "pgvector"}:
            raise ValueError("VECTOR_BACKEND deve ser 'chroma' ou 'pgvector'.")
        if self.kag_graph_backend not in {"postgres", "neptune"}:
            raise ValueError(
                "KAG_GRAPH_BACKEND deve ser 'postgres' (local) ou 'neptune'."
            )
        if "*" in self.allowed_origins and len(self.allowed_origins) > 1:
            raise ValueError("ALLOWED_ORIGINS não pode misturar '*' com origens explícitas.")

        # Se a senha de bootstrap foi definida (em qualquer ambiente), ela
        # precisa ter forca minima. O bootstrap SEM senha nao cria admin,
        # entao string vazia continua valida (opt-out explicito).
        if self.bootstrap_admin_password:
            weak = {
                "123456", "12345678", "password", "senha", "admin",
                "troca-depois", "troque", "mudar", "change-me", "changeme",
            }
            if (
                len(self.bootstrap_admin_password) < 12
                or self.bootstrap_admin_password.lower() in weak
            ):
                raise ValueError(
                    "BOOTSTRAP_ADMIN_PASSWORD precisa ter 12+ caracteres e nao pode "
                    "estar na lista de senhas triviais (123456, admin, password, ...)."
                )

        if not self.is_production:
            return

        if not self.bootstrap_admin_password:
            # Em producao recusar subir sem bootstrap evita o cenario silencioso
            # de deploy sem admin algum: o operador precisa decidir explicitamente
            # (seja definindo a senha, seja criando o admin fora do app).
            raise ValueError(
                "BOOTSTRAP_ADMIN_PASSWORD obrigatorio em producao. "
                "Defina a senha ou provisione o admin via migracao/console."
            )

        if len(self.jwt_secret) < 32 or self.jwt_secret.startswith("development-"):
            raise ValueError(
                "JWT_SECRET deve ter pelo menos 32 caracteres aleatórios em produção."
            )
        if not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("Produção exige PostgreSQL assíncrono em DATABASE_URL.")
        if "*" in self.allowed_origins:
            raise ValueError("ALLOWED_ORIGINS não pode ser '*' em produção.")
        if "*" in self.allowed_hosts:
            raise ValueError(
                "Defina ALLOWED_HOSTS com os domínios reais em produção "
                "para bloquear ataques de Host header."
            )
        if not self.cookie_secure:
            # Sem Secure o refresh e o CSRF token trafegam em claro sob
            # qualquer downgrade para HTTP: em produção isso é indefensável.
            raise ValueError(
                "COOKIE_SECURE=true é obrigatório em produção. "
                "Termine o TLS no proxy e ative a flag."
            )
        if not self.trusted_proxy_cidrs:
            # Sem essa lista, qualquer cliente forjaria X-Forwarded-For e
            # o rate limit por IP viraria decoração.
            raise ValueError(
                "TRUSTED_PROXY_CIDRS não pode ficar vazio em produção: "
                "defina as redes reais do balanceador/ingresso."
            )
        if self.audit_retention_days < 0:
            raise ValueError("AUDIT_RETENTION_DAYS não pode ser negativo.")
        if self.kag_enabled and self.kag_graph_backend == "neptune" and not self.neptune_endpoint:
            raise ValueError(
                "KAG_GRAPH_BACKEND=neptune exige NEPTUNE_ENDPOINT em produção."
            )

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}

    @property
    def api_docs_enabled(self) -> bool:
        """Swagger/ReDoc ficam fechados em produção salvo opt-in explícito."""
        raw = self.enable_api_docs_raw.strip().lower()
        if raw:
            return raw in {"1", "true", "yes", "on"}
        return not self.is_production

    @property
    def cors_allow_credentials(self) -> bool:
        """Cookies só podem cruzar origens quando elas são explícitas."""
        return "*" not in self.allowed_origins

    @property
    def refresh_token_seconds(self) -> int:
        return self.refresh_token_days * 24 * 60 * 60

    @property
    def max_json_body_bytes(self) -> int:
        return self.max_json_body_kb * 1024

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def tenant_upload_quota_bytes(self) -> int:
        return self.tenant_upload_quota_mb * 1024 * 1024

    @property
    def approx_chunk_tokens(self) -> int:
        """Equivalencia aproximada em tokens, apenas para documentacao."""
        return int(self.chunk_chars / CHARS_PER_TOKEN_ESTIMATE)

    def ensure_directories(self) -> None:
        """Cria diretorios locais necessarios."""
        self.chroma_path.mkdir(parents=True, exist_ok=True)
        self.upload_path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna uma instancia compartilhada das configuracoes."""
    return Settings()
