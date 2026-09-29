import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, NgZone, inject } from '@angular/core';
import { Router } from '@angular/router';
import {
  BehaviorSubject,
  Observable,
  catchError,
  finalize,
  firstValueFrom,
  map,
  of,
  shareReplay,
  switchMap,
  tap,
  throwError
} from 'rxjs';
import { LogoutResponse, TokenResponse, UserProfile } from './models';

export const CSRF_HEADER = 'X-CSRF-Token';
const CSRF_COOKIE = 'rag_csrf';

/** Renova o token um pouco antes de expirar, com piso para tokens curtos. */
const RENEWAL_MARGIN_SECONDS = 60;
const IDLE_CHECK_INTERVAL_MS = 30_000;
const ACTIVITY_EVENTS = ['click', 'keydown', 'mousemove', 'scroll', 'touchstart'];

export type LogoutReason = 'inatividade' | 'expirada';

/**
 * Sessao do usuario.
 *
 * O access token vive apenas em memoria: nada de localStorage ou
 * sessionStorage, que sao legiveis por qualquer script injetado na pagina. A
 * continuidade entre recarregamentos vem do cookie httpOnly de refresh, que o
 * JavaScript nao consegue ler.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly zone = inject(NgZone);

  private accessToken: string | null = null;
  private renewalTimer?: ReturnType<typeof setTimeout>;
  private idleWatcher?: ReturnType<typeof setInterval>;
  private renewal?: Observable<string>;
  private idleMinutes = 20;
  private lastActivity = Date.now();
  private trackingActivity = false;

  private readonly profileSubject = new BehaviorSubject<UserProfile | null>(null);
  readonly profile$ = this.profileSubject.asObservable();

  get token(): string | null {
    return this.accessToken;
  }

  get profile(): UserProfile | null {
    return this.profileSubject.value;
  }

  get authenticated(): boolean {
    return Boolean(this.accessToken && this.profileSubject.value);
  }

  /** Metade legivel do double-submit; o par httpOnly fica com o navegador. */
  get csrfToken(): string {
    const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`));
    return match ? decodeURIComponent(match[1]) : '';
  }

  hasScope(scope: string): boolean {
    return this.profileSubject.value?.scopes.includes(scope) ?? false;
  }

  hasEveryScope(scopes: string[]): boolean {
    return scopes.every(scope => this.hasScope(scope));
  }

  /**
   * Restaura a sessao na abertura da aplicacao.
   *
   * Roda como APP_INITIALIZER para que os guards ja encontrem a sessao pronta
   * e o usuario nao veja um piscar da tela de login a cada F5.
   */
  restore(): Promise<void> {
    if (!this.csrfToken) return Promise.resolve();
    return firstValueFrom(this.ensureSession())
      .then(() => undefined)
      .catch(() => undefined);
  }

  login(tenant: string, username: string, password: string): Observable<UserProfile> {
    const body = new HttpParams()
      .set('username', `${tenant.trim()}/${username.trim()}`)
      .set('password', password);
    return this.http
      .post<TokenResponse>('/api/auth/token', body.toString(), {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
      })
      .pipe(switchMap(response => this.adopt(response)));
  }

  loadProfile(): Observable<UserProfile> {
    return this.http.get<UserProfile>('/api/auth/me').pipe(
      tap(profile => {
        this.profileSubject.next(profile);
        this.idleMinutes = profile.session_idle_minutes || this.idleMinutes;
        this.watchInactivity();
      })
    );
  }

  /** Garante uma sessao valida, renovando pelo cookie quando necessario. */
  ensureSession(): Observable<boolean> {
    if (this.authenticated) return of(true);
    const withProfile = this.accessToken
      ? this.loadProfile()
      : this.renew().pipe(switchMap(() => this.loadProfile()));
    return withProfile.pipe(
      map(() => true),
      catchError(() => {
        this.discard();
        return of(false);
      })
    );
  }

  /**
   * Troca o refresh token por um par novo.
   *
   * Chamadas simultaneas compartilham a mesma requisicao: sem isso, varias
   * respostas 401 em paralelo disparariam rotacoes concorrentes e o backend
   * trataria as sobras como reuso de token, derrubando a sessao inteira.
   */
  renew(): Observable<string> {
    if (this.renewal) return this.renewal;

    const csrf = this.csrfToken;
    if (!csrf) return throwError(() => new Error('sessao_ausente'));

    this.renewal = this.http
      .post<TokenResponse>('/api/auth/refresh', {}, { headers: { [CSRF_HEADER]: csrf } })
      .pipe(
        tap(response => {
          this.accessToken = response.access_token;
          this.scheduleRenewal(response.expires_in);
        }),
        map(response => response.access_token),
        finalize(() => (this.renewal = undefined)),
        shareReplay({ bufferSize: 1, refCount: false })
      );
    return this.renewal;
  }

  logout(reason?: LogoutReason): void {
    const csrf = this.csrfToken;
    const done = () => this.leaveToLogin(reason);
    if (!csrf) {
      done();
      return;
    }
    this.http
      .post<LogoutResponse>('/api/auth/logout', {}, { headers: { [CSRF_HEADER]: csrf } })
      .subscribe({ next: done, error: done });
  }

  /** Encerra a sessao em todos os dispositivos do usuario. */
  logoutEverywhere(): Observable<LogoutResponse> {
    return this.http
      .post<LogoutResponse>('/api/auth/logout-all', {})
      .pipe(tap(() => this.leaveToLogin()));
  }

  /** Sessao perdida no meio do uso: limpa tudo e volta para o login. */
  sessionExpired(): void {
    if (!this.accessToken && !this.profileSubject.value) return;
    this.leaveToLogin('expirada');
  }

  private adopt(response: TokenResponse): Observable<UserProfile> {
    this.accessToken = response.access_token;
    this.lastActivity = Date.now();
    this.scheduleRenewal(response.expires_in);
    return this.loadProfile();
  }

  private leaveToLogin(reason?: LogoutReason): void {
    this.discard();
    void this.router.navigate(['/login'], {
      queryParams: reason ? { motivo: reason } : {}
    });
  }

  private discard(): void {
    this.accessToken = null;
    this.profileSubject.next(null);
    if (this.renewalTimer) clearTimeout(this.renewalTimer);
    if (this.idleWatcher) clearInterval(this.idleWatcher);
    this.renewalTimer = undefined;
    this.idleWatcher = undefined;
  }

  private scheduleRenewal(expiresIn: number): void {
    if (this.renewalTimer) clearTimeout(this.renewalTimer);
    const delay = Math.max(expiresIn - RENEWAL_MARGIN_SECONDS, 30) * 1000;
    this.zone.runOutsideAngular(() => {
      this.renewalTimer = setTimeout(() => {
        this.renew().subscribe({ error: () => this.zone.run(() => this.sessionExpired()) });
      }, delay);
    });
  }

  private watchInactivity(): void {
    this.lastActivity = Date.now();
    if (!this.trackingActivity) {
      this.trackingActivity = true;
      // Fora da zona do Angular: mousemove dispararia deteccao de mudanca a
      // cada pixel percorrido.
      this.zone.runOutsideAngular(() => {
        for (const event of ACTIVITY_EVENTS) {
          document.addEventListener(event, this.markActivity, { passive: true });
        }
      });
    }
    if (this.idleWatcher) return;
    this.zone.runOutsideAngular(() => {
      this.idleWatcher = setInterval(() => {
        if (!this.authenticated) return;
        if (Date.now() - this.lastActivity < this.idleMinutes * 60_000) return;
        this.zone.run(() => this.logout('inatividade'));
      }, IDLE_CHECK_INTERVAL_MS);
    });
  }

  private readonly markActivity = (): void => {
    this.lastActivity = Date.now();
  };
}
