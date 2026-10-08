import { HttpClient, HttpEvent, HttpEventType, HttpRequest } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, firstValueFrom, map } from 'rxjs';
import {
  AuditEvent,
  ChatResponse,
  DeleteResponse,
  DocumentSummary,
  DocumentStatusResponse,
  HealthResponse,
  KagRelation,
  KnowledgeBaseStatus,
  ReadyResponse,
  RegulatoryStatus,
  StreamEvent,
  SystemStatus,
  UploadResponse,
  UserProfile,
  UserSummary
} from './models';
import { AuthService, CSRF_HEADER } from './auth.service';

/** Lancado quando o usuario cancela a resposta em andamento. */
export class StreamCancelledError extends Error {
  constructor() {
    super('stream_cancelled');
    this.name = 'StreamCancelledError';
  }
}

/** Lancado quando a conexao cai no meio do streaming. */
export class StreamConnectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'StreamConnectionError';
  }
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);
  private readonly baseUrl = '/api';

  health(): Observable<HealthResponse> {
    return this.http.get<HealthResponse>(`${this.baseUrl}/health`);
  }

  ready(): Observable<ReadyResponse> {
    return this.http.get<ReadyResponse>(`${this.baseUrl}/ready`);
  }

  status(): Observable<SystemStatus> {
    return this.http.get<SystemStatus>(`${this.baseUrl}/status`);
  }

  knowledgeBaseStatus(): Observable<KnowledgeBaseStatus> {
    return this.http.get<KnowledgeBaseStatus>(`${this.baseUrl}/knowledge-base/status`);
  }

  documents(): Observable<DocumentSummary[]> {
    return this.http.get<DocumentSummary[]>(`${this.baseUrl}/documents`);
  }

  upload(file: File): Observable<UploadResponse> {
    const data = new FormData();
    data.append('file', file);
    return this.http.post<UploadResponse>(`${this.baseUrl}/documents/upload`, data);
  }

  /**
   * Upload com progresso incremental.
   *
   * A cada evento HTTP intermediário emite ``{ progress: 0..100 }``; ao
   * receber a resposta final emite ``{ progress: 100, response }``. O
   * consumidor pode ligar a barra de progresso na primeira emissão e ler o
   * ``UploadResponse`` na última.
   */
  uploadWithProgress(
    file: File
  ): Observable<{ progress: number; response?: UploadResponse }> {
    const data = new FormData();
    data.append('file', file);
    const request = new HttpRequest('POST', `${this.baseUrl}/documents/upload`, data, {
      reportProgress: true
    });
    return this.http.request<UploadResponse>(request).pipe(
      map((event: HttpEvent<UploadResponse>) => {
        if (event.type === HttpEventType.UploadProgress && event.total) {
          return { progress: Math.round((100 * event.loaded) / event.total) };
        }
        if (event.type === HttpEventType.Response) {
          return { progress: 100, response: event.body ?? undefined };
        }
        return { progress: 0 };
      })
    );
  }

  deleteDocument(documentId: string): Observable<DeleteResponse> {
    return this.http.delete<DeleteResponse>(
      `${this.baseUrl}/documents/${encodeURIComponent(documentId)}`
    );
  }

  updateDocumentStatus(
    documentId: string,
    status: RegulatoryStatus
  ): Observable<DocumentStatusResponse> {
    return this.http.patch<DocumentStatusResponse>(
      `${this.baseUrl}/documents/${encodeURIComponent(documentId)}/status`,
      { status }
    );
  }

  kagRelations(
    status?: 'validada' | 'pendente_de_validacao' | 'rejeitada'
  ): Observable<KagRelation[]> {
    const params: Record<string, string> = {};
    if (status) params['status'] = status;
    return this.http.get<KagRelation[]>(`${this.baseUrl}/knowledge/relations`, {
      params
    });
  }

  updateKagRelationStatus(
    relationId: string,
    status: 'validada' | 'pendente_de_validacao' | 'rejeitada'
  ): Observable<KagRelation> {
    return this.http.patch<KagRelation>(
      `${this.baseUrl}/knowledge/relations/${encodeURIComponent(relationId)}`,
      { status }
    );
  }

  auditEvents(limit = 100): Observable<AuditEvent[]> {
    return this.http.get<AuditEvent[]>(`${this.baseUrl}/audit`, {
      params: { limit }
    });
  }

  users(): Observable<UserSummary[]> {
    return this.http.get<UserSummary[]>(`${this.baseUrl}/admin/users`);
  }

  createUser(username: string, password: string, role: string): Observable<UserProfile> {
    return this.http.post<UserProfile>(`${this.baseUrl}/admin/users`, {
      username,
      password,
      role
    });
  }

  setUserActive(userId: string, active: boolean): Observable<UserSummary> {
    return this.http.patch<UserSummary>(
      `${this.baseUrl}/admin/users/${encodeURIComponent(userId)}`,
      { active }
    );
  }

  changePassword(currentPassword: string, newPassword: string): Observable<unknown> {
    return this.http.post(`${this.baseUrl}/auth/password`, {
      current_password: currentPassword,
      new_password: newPassword
    });
  }

  sendFeedback(requestId: string, useful: boolean, comment?: string): Observable<{ recorded: boolean }> {
    return this.http.post<{ recorded: boolean }>(`${this.baseUrl}/chat/feedback`, {
      request_id: requestId,
      useful,
      comment: comment || null
    });
  }

  /** Endpoint JSON original, preservado como fallback. */
  chat(question: string): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(`${this.baseUrl}/chat`, { question });
  }

  chatOnce(question: string): Promise<ChatResponse> {
    return firstValueFrom(this.chat(question));
  }

  /** True quando o navegador suporta ler o corpo da resposta em streaming. */
  supportsStreaming(): boolean {
    return typeof fetch === 'function' && typeof ReadableStream === 'function';
  }

  /**
   * POST /api/chat/stream lido como NDJSON.
   *
   * Cada linha completa e um evento JSON. O `signal` permite cancelar a
   * geracao em andamento; nesse caso o metodo lanca `StreamCancelledError`.
   */
  async *chatStream(question: string, signal: AbortSignal): AsyncGenerator<StreamEvent> {
    let response: Response;
    try {
      response = await this.openStream(question, signal);
      // O streaming usa fetch puro e nao passa pelo interceptor, entao a
      // renovacao do token expirado precisa acontecer aqui.
      if (response.status === 401) {
        await firstValueFrom(this.auth.renew());
        response = await this.openStream(question, signal);
      }
    } catch (error) {
      if (signal.aborted) throw new StreamCancelledError();
      throw new StreamConnectionError('Nao foi possivel abrir a conexao de streaming.');
    }

    if (response.status === 401) {
      this.auth.sessionExpired();
      throw new StreamConnectionError('stream_http_401');
    }

    if (!response.ok || !response.body) {
      // Deixa o chamador cair no fallback JSON, que traz o erro estruturado.
      throw new StreamConnectionError(`stream_http_${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let newline = buffer.indexOf('\n');
        while (newline >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (line) {
            const parsed = this.parseEvent(line);
            if (parsed) yield parsed;
          }
          newline = buffer.indexOf('\n');
        }
      }

      const tail = buffer.trim();
      if (tail) {
        const parsed = this.parseEvent(tail);
        if (parsed) yield parsed;
      }
    } catch (error) {
      if (signal.aborted) throw new StreamCancelledError();
      throw new StreamConnectionError('A conexao foi interrompida durante a resposta.');
    } finally {
      reader.releaseLock();
    }
  }

  private openStream(question: string, signal: AbortSignal): Promise<Response> {
    const token = this.auth.token;
    const csrf = this.auth.csrfToken;
    return fetch(`${this.baseUrl}/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/x-ndjson',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(csrf ? { [CSRF_HEADER]: csrf } : {})
      },
      body: JSON.stringify({ question }),
      credentials: 'same-origin',
      signal
    });
  }

  private parseEvent(line: string): StreamEvent | null {
    try {
      return JSON.parse(line) as StreamEvent;
    } catch {
      return null;
    }
  }
}
