import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';
import { ApiService } from '../../api.service';
import { humanizeError } from '../../errors';
import { AuditEvent } from '../../models';

/** Ações cujo resultado merece destaque visual na trilha. */
const ALERT_STATUSES = new Set([
  'bad_password',
  'locked',
  'locked_out',
  'unknown_user',
  'reuse_detected'
]);

@Component({
  selector: 'app-audit',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './audit.component.html'
})
export class AuditComponent implements OnInit {
  private readonly api = inject(ApiService);

  events: AuditEvent[] = [];
  loading = false;
  error = '';
  limit = 100;

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading = true;
    this.error = '';
    this.api
      .auditEvents(this.limit)
      .pipe(finalize(() => (this.loading = false)))
      .subscribe({
        next: events => (this.events = events),
        error: (response: HttpErrorResponse) => {
          this.events = [];
          this.error = humanizeError(response).message;
        }
      });
  }

  isAlert(event: AuditEvent): boolean {
    return ALERT_STATUSES.has(event.status);
  }
}
