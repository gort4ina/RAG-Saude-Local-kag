import { HttpErrorResponse, HttpInterceptorFn, HttpRequest } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, switchMap, throwError } from 'rxjs';
import { AuthService, CSRF_HEADER } from './auth.service';

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

/** Rotas que ja lidam com a propria sessao e nao devem ser reprocessadas. */
const SESSION_ENDPOINTS = ['/api/auth/token', '/api/auth/refresh', '/api/auth/logout'];

function withCredentials(request: HttpRequest<unknown>, auth: AuthService): HttpRequest<unknown> {
  const headers: Record<string, string> = {};
  if (auth.token) headers['Authorization'] = `Bearer ${auth.token}`;
  if (!SAFE_METHODS.has(request.method) && auth.csrfToken) {
    headers[CSRF_HEADER] = auth.csrfToken;
  }
  return Object.keys(headers).length ? request.clone({ setHeaders: headers }) : request;
}

/**
 * Assina cada requisicao e recupera sessoes expiradas.
 *
 * Um 401 costuma significar apenas que o access token (curto) venceu. Nesse
 * caso o interceptor renova pelo cookie e repete a chamada uma unica vez; se a
 * renovacao tambem falhar, a sessao acabou de verdade.
 */
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);
  const isSessionCall = SESSION_ENDPOINTS.some(path => request.url.startsWith(path));

  return next(withCredentials(request, auth)).pipe(
    catchError((error: HttpErrorResponse) => {
      if (error.status !== 401 || isSessionCall) return throwError(() => error);

      return auth.renew().pipe(
        switchMap(() => next(withCredentials(request, auth))),
        catchError(() => {
          auth.sessionExpired();
          return throwError(() => error);
        })
      );
    })
  );
};
