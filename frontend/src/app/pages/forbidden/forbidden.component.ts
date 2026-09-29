import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-forbidden',
  standalone: true,
  imports: [RouterLink],
  template: `
    <main class="page">
      <section class="panel centered">
        <h1>Sem permissão</h1>
        <p>Sua conta não possui o perfil necessário para abrir esta área.</p>
        <a class="link-button" routerLink="/consulta">Voltar para a consulta</a>
      </section>
    </main>
  `
})
export class ForbiddenComponent {}
