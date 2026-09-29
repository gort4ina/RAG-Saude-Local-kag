import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Subscription, interval, startWith, switchMap } from 'rxjs';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import { HealthResponse, SystemStatus } from './models';

const POLL_INTERVAL_MS = 15_000;

/**
 * Estado operacional compartilhado entre a barra superior e a area de trabalho.
 *
 * Centralizar a consulta evita que duas telas abram o mesmo polling em
 * paralelo e dobrem a carga sobre o backend.
 */
@Injectable({ providedIn: 'root' })
export class StatusService {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  private readonly healthSubject = new BehaviorSubject<HealthResponse | null>(null);
  private readonly statusSubject = new BehaviorSubject<SystemStatus | null>(null);
  private polling?: Subscription;

  readonly health$ = this.healthSubject.asObservable();
  readonly status$ = this.statusSubject.asObservable();

  get status(): SystemStatus | null {
    return this.statusSubject.value;
  }

  start(): void {
    if (this.polling) return;

    // Sem o scope de status o usuario so enxerga a disponibilidade basica.
    if (!this.auth.hasScope('status:read')) {
      this.polling = interval(POLL_INTERVAL_MS)
        .pipe(
          startWith(0),
          switchMap(() => this.api.health())
        )
        .subscribe({
          next: health => this.healthSubject.next(health),
          error: () => this.healthSubject.next(null)
        });
      return;
    }

    this.polling = interval(POLL_INTERVAL_MS)
      .pipe(
        startWith(0),
        switchMap(() => this.api.status())
      )
      .subscribe({
        next: status => {
          this.statusSubject.next(status);
          this.healthSubject.next(summarize(status));
        },
        error: () => {
          this.statusSubject.next(null);
          this.healthSubject.next(null);
        }
      });
  }

  stop(): void {
    this.polling?.unsubscribe();
    this.polling = undefined;
    this.healthSubject.next(null);
    this.statusSubject.next(null);
  }
}

function summarize(status: SystemStatus): HealthResponse {
  return {
    status:
      status.chat_model_installed && status.embedding_model_installed ? 'ok' : 'degraded',
    ollama: status.components.find(item => item.name === 'ollama')?.ok
      ? 'online'
      : 'offline',
    chat_model: status.chat_model,
    embedding_model: status.embedding_model
  };
}
