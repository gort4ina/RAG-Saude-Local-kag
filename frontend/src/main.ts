import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { APP_INITIALIZER, inject } from '@angular/core';
import { provideRouter, withInMemoryScrolling } from '@angular/router';
import { AppComponent } from './app/app.component';
import { APP_ROUTES } from './app/app.routes';
import { authInterceptor } from './app/auth.interceptor';
import { AuthService } from './app/auth.service';

bootstrapApplication(AppComponent, {
  providers: [
    provideHttpClient(withInterceptors([authInterceptor])),
    provideRouter(APP_ROUTES, withInMemoryScrolling({ scrollPositionRestoration: 'top' })),
    {
      // Tenta restaurar a sessao pelo cookie httpOnly antes do primeiro
      // roteamento; assim os guards ja decidem com a sessao resolvida.
      provide: APP_INITIALIZER,
      multi: true,
      useFactory: () => {
        const auth = inject(AuthService);
        return () => auth.restore();
      }
    }
  ]
}).catch((error: unknown) => console.error(error));
