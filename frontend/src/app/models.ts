export interface Source {
  document: string;
  page: number | null;
  chunk: number;
  score: number;
  lexical_score: number;
  excerpt: string;
  authority?: string | null;
  regulation_number?: string | null;
  publication_date?: string | null;
  effective_date?: string | null;
  status?: string | null;
  status_label: string;
  article?: string | null;
  section?: string | null;
  source_url?: string | null;
  document_version?: string | null;
  cited: boolean;
}

export interface KagRelation {
  id: string;
  relation: string;
  subject: string;
  subject_kind: string;
  object: string;
  object_kind: string;
  source_reference?: string | null;
  source_document_id?: string | null;
  validation_status: string;
  extracted_by?: string;
  confidence?: number;
  source_span?: string | null;
  validated_by?: string | null;
  validated_at?: string | null;
}

export interface ChatResponse {
  answer: string;
  sources: Source[];
  retrieved_sources: Source[];
  relations?: KagRelation[];
  requires_human_review: boolean;
  review_reasons: string[];
  grounded: boolean;
  disclaimer: string;
  request_id?: string;
  duration_ms?: number;
  collection_count?: number;
  embed_ms?: number;
  retrieval_ms?: number;
  graph_ms?: number;
  chat_ms?: number;
  load_duration_ms?: number;
  eval_count?: number;
  tokens_per_second?: number;
}

export interface UploadResponse {
  document_id: string;
  filename: string;
  pages: number;
  total_pages: number;
  chunks: number;
  chars_per_page: Record<string, number>;
  authority?: string | null;
  regulation_number?: string | null;
  status?: string | null;
  status_label: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
}

export interface UserProfile {
  user_id: string;
  tenant_id: string;
  username: string;
  role: string;
  scopes: string[];
  /** Minutos de inatividade antes do encerramento automatico da sessao. */
  session_idle_minutes: number;
}

export interface UserSummary {
  user_id: string;
  username: string;
  role: string;
  active: boolean;
  locked: boolean;
  last_login_at?: string | null;
  created_at: string;
}

export interface AuditEvent {
  id: string;
  request_id: string;
  user_id: string;
  action: string;
  status: string;
  query_text?: string | null;
  details: Record<string, unknown>;
  duration_ms: number;
  ip_address?: string | null;
  created_at: string;
}

export interface LogoutResponse {
  revoked: boolean;
}

export interface DeleteResponse {
  document_id: string;
  deleted: boolean;
}

export interface DocumentSummary {
  document_id: string;
  filename: string;
  chunks: number;
  authority?: string | null;
  regulation_number?: string | null;
  status?: string | null;
  status_label: string;
  publication_date?: string | null;
}

export type RegulatoryStatus =
  | 'vigente_confirmada'
  | 'revogada_confirmada'
  | 'vigencia_nao_verificada';

export interface DocumentStatusResponse {
  document_id: string;
  status: RegulatoryStatus;
  status_label: string;
  updated_chunks: number;
  validated_by?: string | null;
  validated_at?: string | null;
}

export interface HealthResponse {
  status: 'ok' | 'degraded';
  ollama: 'online' | 'offline';
  chat_model: string;
  embedding_model: string;
}

export interface ReadyResponse {
  ready: boolean;
  reasons: string[];
}

export interface ComponentStatus {
  name: string;
  ok: boolean;
  detail: string;
}

export interface LoadedModel {
  name: string;
  size_mb: number;
  size_vram_mb: number;
  on_gpu: boolean;
}

export interface SystemStatus {
  app_name: string;
  app_env: string;
  components: ComponentStatus[];
  chat_model: string;
  embedding_model: string;
  chat_model_installed: boolean;
  embedding_model_installed: boolean;
  collection_name: string;
  documents_count: number;
  chunks_count: number;
  bm25_index_size: number;
  hybrid_search_enabled: boolean;
  loaded_models: LoadedModel[];
  gpu_in_use: boolean;
  gpu_detail: string;
}

export interface KnowledgeBaseStatus {
  ready: boolean;
  collection_name: string;
  embedding_model: string;
  documents_count: number;
  chunks_count: number;
  bm25_index_size: number;
  unverified_documents: number;
  reason: string;
}

export interface ApiError {
  code: string;
  detail: string;
  request_id?: string;
}

/** Eventos NDJSON emitidos por POST /api/chat/stream. */
export type StreamEvent =
  | {
      type: 'metadata';
      request_id: string;
      sources: Source[];
      relations?: KagRelation[];
    }
  | { type: 'token'; content: string }
  | {
      type: 'done';
      duration_ms: number;
      grounded: boolean;
      requires_human_review: boolean;
      review_reasons: string[];
      /** Texto final apos a auditoria de citacoes; substitui o que foi streamado. */
      answer?: string;
      sources: Source[];
      disclaimer: string;
      chat_ms?: number;
      load_duration_ms?: number;
      tokens_per_second?: number;
    }
  | { type: 'error'; code: string; detail: string; request_id?: string };

export interface Message {
  role: 'user' | 'assistant';
  content: string;
  sources?: Source[];
  relations?: KagRelation[];
  requires_human_review: boolean;
  reviewReasons?: string[];
  grounded?: boolean;
  errorCode?: string;
  streaming?: boolean;
  cancelled?: boolean;
  durationMs?: number;
  tokensPerSecond?: number;
}
