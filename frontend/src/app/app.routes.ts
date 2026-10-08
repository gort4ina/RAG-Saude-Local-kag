import { Routes } from '@angular/router';
import { authGuard, guestGuard, scopeGuard } from './guards/auth.guard';

/**
 * Mapa de rotas.
 *
 * Nenhuma tela autenticada fica fora do ``ShellComponent``, e o shell exige o
 * ``authGuard`` na entrada e em cada navegacao filha. As areas sensiveis pedem
 * ainda o scope correspondente, o mesmo exigido pelo backend na rota da API.
 */
export const APP_ROUTES: Routes = [
  {
    path: 'login',
    canActivate: [guestGuard],
    loadComponent: () =>
      import('./pages/login/login.component').then(module => module.LoginComponent)
  },
  {
    path: '',
    canActivate: [authGuard],
    canActivateChild: [authGuard],
    loadComponent: () =>
      import('./layout/shell.component').then(module => module.ShellComponent),
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'consulta' },
      {
        path: 'consulta',
        loadComponent: () =>
          import('./pages/workspace/workspace.component').then(
            module => module.WorkspaceComponent
          )
      },
      {
        path: 'usuarios',
        canActivate: [scopeGuard('admin:manage')],
        loadComponent: () =>
          import('./pages/admin/admin-users.component').then(
            module => module.AdminUsersComponent
          )
      },
      {
        path: 'auditoria',
        canActivate: [scopeGuard('audit:read')],
        loadComponent: () =>
          import('./pages/audit/audit.component').then(module => module.AuditComponent)
      },
      {
        path: 'kag',
        canActivate: [scopeGuard('documents:read')],
        loadComponent: () =>
          import('./pages/admin/admin-kag.component').then(
            module => module.AdminKagComponent
          )
      },
      {
        path: 'sem-permissao',
        loadComponent: () =>
          import('./pages/forbidden/forbidden.component').then(
            module => module.ForbiddenComponent
          )
      }
    ]
  },
  { path: '**', redirectTo: '' }
];
