import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';
import { ApiService } from '../../api.service';
import { AuthService } from '../../auth.service';
import { humanizeError } from '../../errors';
import { KagRelation } from '../../models';

@Component({
  selector: 'app-admin-kag',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './admin-kag.component.html'
})
export class AdminKagComponent implements OnInit {
  private readonly api = inject(ApiService);
  readonly auth = inject(AuthService);

  relations: KagRelation[] = [];
  loading = false;
  notice = '';
  noticeType: 'error' | 'success' = 'success';
  filter: '' | 'validada' | 'pendente_de_validacao' | 'rejeitada' = '';

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading = true;
    this.api
      .kagRelations(this.filter || undefined)
      .pipe(finalize(() => (this.loading = false)))
      .subscribe({
        next: relations => (this.relations = relations),
        error: (error: HttpErrorResponse) => this.report(error)
      });
  }

  decide(relation: KagRelation, status: 'validada' | 'rejeitada' | 'pendente_de_validacao'): void {
    this.api.updateKagRelationStatus(relation.id, status).subscribe({
      next: updated => {
        this.noticeType = 'success';
        this.notice = `Relação ${updated.subject} → ${updated.object}: ${updated.validation_status}.`;
        this.refresh();
      },
      error: (error: HttpErrorResponse) => this.report(error)
    });
  }

  private report(error: HttpErrorResponse): void {
    this.noticeType = 'error';
    this.notice = humanizeError(error).message;
  }
}
