import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';
import { ApiService } from '../../api.service';
import { AuthService } from '../../auth.service';
import { humanizeError } from '../../errors';
import { UserSummary } from '../../models';

@Component({
  selector: 'app-admin-users',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './admin-users.component.html'
})
export class AdminUsersComponent implements OnInit {
  private readonly api = inject(ApiService);
  readonly auth = inject(AuthService);

  users: UserSummary[] = [];
  loading = false;
  saving = false;
  notice = '';
  noticeType: 'error' | 'success' = 'success';

  newUsername = '';
  newPassword = '';
  newRole = 'user';

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading = true;
    this.api
      .users()
      .pipe(finalize(() => (this.loading = false)))
      .subscribe({
        next: users => (this.users = users),
        error: (error: HttpErrorResponse) => this.report(error)
      });
  }

  create(): void {
    if (this.saving || !this.newUsername.trim() || !this.newPassword) return;
    this.saving = true;
    this.notice = '';
    this.api
      .createUser(this.newUsername.trim(), this.newPassword, this.newRole)
      .pipe(finalize(() => (this.saving = false)))
      .subscribe({
        next: created => {
          this.noticeType = 'success';
          this.notice = `Usuário ${created.username} criado.`;
          this.newUsername = '';
          this.newPassword = '';
          this.newRole = 'user';
          this.refresh();
        },
        error: (error: HttpErrorResponse) => this.report(error)
      });
  }

  toggle(user: UserSummary): void {
    const action = user.active ? 'desativar' : 'reativar';
    if (!window.confirm(`Deseja ${action} o acesso de ${user.username}?`)) return;

    this.api.setUserActive(user.user_id, !user.active).subscribe({
      next: updated => {
        this.noticeType = 'success';
        this.notice = updated.active
          ? `${updated.username} pode acessar novamente.`
          : `${updated.username} foi desativado e teve as sessões encerradas.`;
        this.refresh();
      },
      error: (error: HttpErrorResponse) => this.report(error)
    });
  }

  isSelf(user: UserSummary): boolean {
    return user.user_id === this.auth.profile?.user_id;
  }

  private report(error: HttpErrorResponse): void {
    this.noticeType = 'error';
    // O 422 da política de senha traz a regra violada em texto; vale mostrar.
    const detail = (error.error as { detail?: unknown })?.detail;
    this.notice =
      error.status === 422 && typeof detail === 'string'
        ? detail
        : error.status === 422
          ? 'A senha não atende à política mínima de segurança.'
          : humanizeError(error).message;
  }
}
