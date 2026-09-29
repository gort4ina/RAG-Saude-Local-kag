import { inject } from '@angular/core';
import { CanActivateFn, Router, UrlTree } from '@angular/router';
import { Observable, map } from 'rxjs';
import { AuthService } from '../auth.service';

/**
 * Guards de rota.
 *
 * Eles decidem apenas o que a interface mostra. Toda rota chamada aqui e
 * protegida de novo no backend por JWT e scope: um usuario que digite a URL na
 * mao ou desative o JavaScript nao ganha acesso a dado nenhum.
 */

/** Exige sessao ativa; tenta renovar pelo cookie antes de recusar. */
export const authGuard: CanActivateFn = (_route, state): Observable<boolean | UrlTree> => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth
    .ensureSession()
    .pipe(map(active => (active ? true : loginRedirect(router, state.url))));
};

/** Impede que quem ja esta logado volte para a tela de login. */
export const guestGuard: CanActivateFn = (): Observable<boolean | UrlTree> => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth
    .ensureSession()
    .pipe(map(active => (active ? router.createUrlTree(['/consulta']) : true)));
};

/** Exige sessao ativa e todos os scopes informados. */
export function scopeGuard(...scopes: string[]): CanActivateFn {
  return (_route, state): Observable<boolean | UrlTree> => {
    const auth = inject(AuthService);
    const router = inject(Router);

    return auth.ensureSession().pipe(
      map(active => {
        if (!active) return loginRedirect(router, state.url);
        return auth.hasEveryScope(scopes) ? true : router.createUrlTree(['/sem-permissao']);
      })
    );
  };
}

function loginRedirect(router: Router, attemptedUrl: string): UrlTree {
  return router.createUrlTree(['/login'], { queryParams: { returnUrl: attemptedUrl } });
}
