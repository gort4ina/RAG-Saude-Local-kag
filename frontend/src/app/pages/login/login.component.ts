import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { AuthService } from '../../auth.service';

const SESSION_NOTICES: Record<string, string> = {
  inatividade: 'Sessão encerrada por inatividade. Entre novamente para continuar.',
  expirada: 'Sua sessão expirou. Entre novamente para continuar.'
};

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './login.component.html'
})
export class LoginComponent implements OnInit {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  tenant = 'local';
  username = '';
  password = '';
  loading = false;
  error = '';
  notice = '';

  private returnUrl = '/consulta';

  ngOnInit(): void {
    const params = this.route.snapshot.queryParamMap;
    this.returnUrl = sanitizeReturnUrl(params.get('returnUrl'));
    this.notice = SESSION_NOTICES[params.get('motivo') ?? ''] ?? '';
  }

  submit(): void {
    if (this.loading) return;
    if (!this.tenant.trim() || !this.username.trim() || !this.password) return;

    this.loading = true;
    this.error = '';
    this.notice = '';
    this.auth.login(this.tenant, this.username, this.password).subscribe({
      next: () => {
        this.password = '';
        this.loading = false;
        void this.router.navigateByUrl(this.returnUrl);
      },
      error: (response: HttpErrorResponse) => {
        this.loading = false;
        this.password = '';
        // A API responde igual para usuario inexistente, senha errada e conta
        // bloqueada; a interface repete essa indefinicao de proposito.
        this.error =
          response.status === 429
            ? 'Muitas tentativas seguidas. Aguarde um minuto antes de tentar de novo.'
            : 'Organização, usuário ou senha inválidos.';
      }
    });
  }
}

/**
 * Aceita apenas caminhos internos.
 *
 * Sem esse filtro, um link como ``/login?returnUrl=https://site-falso`` levaria
 * o usuario recem-autenticado para fora da aplicacao.
 */
function sanitizeReturnUrl(value: string | null): string {
  if (!value) return '/consulta';
  if (!value.startsWith('/') || value.startsWith('//')) return '/consulta';
  return value;
}
