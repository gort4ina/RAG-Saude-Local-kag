import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  AfterViewChecked,
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  OnInit,
  ViewChild,
  inject
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';
import {
  ApiService,
  StreamCancelledError,
  StreamConnectionError
} from '../../api.service';
import { AuthService } from '../../auth.service';
import { ERROR_MESSAGES, humanizeError, messageForCode } from '../../errors';
import { DocumentSummary, Message, RegulatoryStatus } from '../../models';
import { StatusService } from '../../status.service';

const ACCEPTED_EXTENSIONS = ['.pdf', '.txt', '.md'];

@Component({
  selector: 'app-workspace',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './workspace.component.html'
})
export class WorkspaceComponent implements OnInit, OnDestroy, AfterViewChecked {
  private readonly api = inject(ApiService);
  readonly auth = inject(AuthService);
  readonly status = inject(StatusService);

  @ViewChild('messagesEl') private messagesEl?: ElementRef<HTMLElement>;

  private abortController?: AbortController;
  private shouldStickToBottom = false;

  question = '';
  messages: Message[] = [];
  documents: DocumentSummary[] = [];
  loading = false;
  uploading = false;
  uploadProgress = 0;
  dropzoneActive = false;
  updatingDocumentId = '';
  notice = '';
  noticeType: 'info' | 'error' | 'success' = 'info';
  sidebarOpen = false;
  systemPanelOpen = true;
  isNarrow = false;

  ngOnInit(): void {
    this.refreshDocuments();
    this.onResize();
  }

  ngAfterViewChecked(): void {
    if (!this.shouldStickToBottom || !this.messagesEl) return;
    const el = this.messagesEl.nativeElement;
    el.scrollTop = el.scrollHeight;
    this.shouldStickToBottom = false;
  }

  ngOnDestroy(): void {
    this.abortController?.abort();
  }

  @HostListener('window:resize')
  onResize(): void {
    this.isNarrow = window.innerWidth <= 900;
    if (!this.isNarrow) this.sidebarOpen = false;
  }

  fileExt(filename: string): string {
    const ext = filename.split('.').pop()?.toUpperCase() || 'DOC';
    return ext.slice(0, 4);
  }

  trackMessage(_index: number, message: Message): string {
    return `${message.role}:${message.content.slice(0, 24)}:${message.durationMs ?? 0}`;
  }

  clearChat(): void {
    if (this.loading) return;
    this.messages = [];
    this.question = '';
  }

  askExample(text: string): void {
    this.question = text;
    void this.send();
  }

  autoResize(el: HTMLTextAreaElement): void {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  onEnter(event: Event): void {
    const keyboard = event as KeyboardEvent;
    if (keyboard.shiftKey) return;
    keyboard.preventDefault();
    void this.send();
  }

  refreshDocuments(): void {
    if (!this.auth.hasScope('documents:read')) return;
    this.api.documents().subscribe({
      next: documents => (this.documents = documents),
      error: () => (this.documents = [])
    });
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.startUpload(file, () => (input.value = ''));
  }

  // ----- Drag-and-drop -----
  onDragOver(event: DragEvent): void {
    // Sem preventDefault o navegador cai no comportamento default de "abrir
    // arquivo na aba", que perde totalmente o upload.
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    if (!this.uploading) this.dropzoneActive = true;
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.dropzoneActive = false;
  }

  onFileDrop(event: DragEvent): void {
    event.preventDefault();
    this.dropzoneActive = false;
    if (this.uploading) return;
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;

    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!ACCEPTED_EXTENSIONS.includes(ext)) {
      this.noticeType = 'error';
      this.notice = `Formato não suportado: ${ext || 'sem extensão'}. Envie PDF, TXT ou MD.`;
      return;
    }
    this.startUpload(file);
  }

  /** Núcleo comum do upload — file input e drag-and-drop desembocam aqui. */
  private startUpload(file: File, onFinish?: () => void): void {
    this.uploading = true;
    this.uploadProgress = 0;
    this.notice = '';

    this.api
      .uploadWithProgress(file)
      .pipe(
        finalize(() => {
          this.uploading = false;
          this.uploadProgress = 0;
          onFinish?.();
        })
      )
      .subscribe({
        next: event => {
          this.uploadProgress = event.progress;
          if (event.response) {
            const result = event.response;
            this.noticeType = 'success';
            this.notice =
              `${result.filename}: ${result.chunks} trecho(s) indexado(s) ` +
              `de ${result.pages}/${result.total_pages} página(s) com texto.`;
            this.refreshDocuments();
          }
        },
        error: (error: HttpErrorResponse) => {
          this.noticeType = 'error';
          this.notice = humanizeError(error).message;
        }
      });
  }

  deleteDocument(document: DocumentSummary): void {
    if (!window.confirm(`Excluir ${document.filename} da base desta organização?`)) return;
    this.api.deleteDocument(document.document_id).subscribe({
      next: () => {
        this.noticeType = 'success';
        this.notice = `${document.filename} foi excluído.`;
        this.refreshDocuments();
      },
      error: (error: HttpErrorResponse) => {
        this.noticeType = 'error';
        this.notice = humanizeError(error).message;
      }
    });
  }

  setDocumentStatus(document: DocumentSummary, status: RegulatoryStatus): void {
    const action =
      status === 'vigente_confirmada'
        ? 'confirmar a vigência'
        : status === 'revogada_confirmada'
          ? 'marcar como revogado'
          : 'remover a confirmação de vigência';
    if (!window.confirm(`Deseja ${action} de ${document.filename}?`)) return;

    this.updatingDocumentId = document.document_id;
    this.notice = '';
    this.api
      .updateDocumentStatus(document.document_id, status)
      .pipe(finalize(() => (this.updatingDocumentId = '')))
      .subscribe({
        next: result => {
          this.noticeType = 'success';
          this.notice = `${document.filename}: ${result.status_label} em ${result.updated_chunks} trecho(s).`;
          this.refreshDocuments();
        },
        error: (error: HttpErrorResponse) => {
          this.noticeType = 'error';
          this.notice = humanizeError(error).message;
        }
      });
  }

  sendFeedback(message: Message, useful: boolean): void {
    if (!message.requestId || message.feedback) return;
    this.api.sendFeedback(message.requestId, useful).subscribe({
      next: () => (message.feedback = useful ? 'useful' : 'not_useful'),
      error: () => {
        this.noticeType = 'error';
        this.notice = 'Não foi possível registrar o feedback.';
      }
    });
  }

  cancel(): void {
    this.abortController?.abort();
  }

  async send(): Promise<void> {
    const value = this.question.trim();
    if (!value || this.loading) return;
    if (value.length < 3) {
      this.messages.push({
        role: 'assistant',
        content: ERROR_MESSAGES['invalid_question'],
        requires_human_review: false,
        errorCode: 'invalid_question'
      });
      return;
    }

    this.messages.push({ role: 'user', content: value, requires_human_review: false });
    this.question = '';
    this.loading = true;
    this.shouldStickToBottom = true;
    this.sidebarOpen = false;

    if (this.api.supportsStreaming()) {
      await this.sendStreaming(value);
    } else {
      await this.sendJson(value);
    }
    this.loading = false;
    this.shouldStickToBottom = true;
  }

  private async sendStreaming(question: string): Promise<void> {
    const controller = new AbortController();
    this.abortController = controller;

    const message: Message = {
      role: 'assistant',
      content: '',
      requires_human_review: false,
      streaming: true
    };
    this.messages.push(message);

    try {
      for await (const event of this.api.chatStream(question, controller.signal)) {
        switch (event.type) {
          case 'metadata':
            message.sources = event.sources;
            message.relations = event.relations ?? [];
            message.requestId = event.request_id;
            break;
          case 'token':
            message.content += event.content;
            this.shouldStickToBottom = true;
            break;
          case 'done':
            // O backend audita as citacoes so depois do ultimo token, entao o
            // texto exibido e trocado pelo canonico (uma resposta sem fonte
            // valida vira recusa).
            if (event.answer !== undefined) {
              message.content = event.answer;
            }
            message.sources = event.sources;
            message.grounded = event.grounded;
            message.requires_human_review = event.requires_human_review;
            message.reviewReasons = event.review_reasons;
            message.durationMs = event.duration_ms;
            message.tokensPerSecond = event.tokens_per_second;
            if (event.request_id) message.requestId = event.request_id;
            break;
          case 'error':
            message.errorCode = event.code;
            message.requires_human_review = true;
            message.content = message.content || messageForCode(event.code, event.detail);
            break;
        }
      }
    } catch (error) {
      if (error instanceof StreamCancelledError) {
        message.cancelled = true;
        message.content = message.content || 'Resposta cancelada.';
        message.errorCode = 'cancelled';
      } else if (error instanceof StreamConnectionError) {
        // Sem nenhum token recebido ainda: tenta o endpoint JSON.
        if (!message.content) {
          this.messages.pop();
          await this.sendJson(question);
        } else {
          message.errorCode = 'stream_connection_error';
          message.requires_human_review = true;
        }
      } else {
        message.errorCode = 'internal_error';
        message.requires_human_review = true;
        message.content = message.content || ERROR_MESSAGES['internal_error'];
      }
    } finally {
      message.streaming = false;
      this.abortController = undefined;
    }
  }

  private async sendJson(question: string): Promise<void> {
    try {
      const response = await this.api.chatOnce(question);
      this.messages.push({
        role: 'assistant',
        content: response.answer,
        sources: response.sources.length ? response.sources : response.retrieved_sources,
        relations: response.relations ?? [],
        grounded: response.grounded,
        requires_human_review: response.requires_human_review ?? false,
        reviewReasons: response.review_reasons,
        durationMs: response.duration_ms,
        tokensPerSecond: response.tokens_per_second,
        requestId: response.request_id
      });
    } catch (error) {
      const { code, message } = humanizeError(error as HttpErrorResponse);
      this.messages.push({
        role: 'assistant',
        content: message,
        requires_human_review: true,
        errorCode: code
      });
    }
  }
}
