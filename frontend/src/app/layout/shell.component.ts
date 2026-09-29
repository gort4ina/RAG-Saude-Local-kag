import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../auth.service';
import { StatusService } from '../status.service';

/**
 * Moldura das telas autenticadas.
 *
 * Fica atras do ``authGuard``, entao tudo que renderiza aqui dentro ja
 * pressupoe sessao valida.
 */
@Component({
  selector: 'app-shell',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './shell.component.html'
})
export class ShellComponent implements OnInit, OnDestroy {
  readonly auth = inject(AuthService);
  readonly status = inject(StatusService);

  ngOnInit(): void {
    this.status.start();
  }

  ngOnDestroy(): void {
    this.status.stop();
  }

  logout(): void {
    this.status.stop();
    this.auth.logout();
  }
}
