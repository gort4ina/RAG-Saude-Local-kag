# Operação segura do MVP

## Primeira configuração

Copie `.env.example` para `.env` e substitua, no mínimo:

```env
APP_ENV=production
POSTGRES_PASSWORD=<valor forte e URL-safe>
JWT_SECRET=<valor aleatório com pelo menos 32 caracteres>
BOOTSTRAP_TENANT_SLUG=local
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=<senha forte com pelo menos 12 caracteres>
```

Gere o segredo JWT com:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Com `APP_ENV=production` a aplicação **se recusa a subir** se qualquer um destes
itens estiver frouxo — a falha é proposital, para que a configuração insegura
apareça no deploy e não em produção:

| Variável | Exigência em produção |
|---|---|
| `JWT_SECRET` | 32+ caracteres e sem o prefixo `development-` |
| `DATABASE_URL` | precisa ser `postgresql+asyncpg://` |
| `ALLOWED_ORIGINS` | não pode ser `*` |
| `ALLOWED_HOSTS` | não pode ser `*`; liste os domínios reais |

Assim que houver TLS, defina também `COOKIE_SECURE=true`. Sem isso o cookie de
refresh trafega em claro.

Suba a stack normalmente. O job `db-migrate` aplica o schema do Alembic e a
API cria o primeiro tenant e administrador de forma idempotente. Alterar a
senha de bootstrap depois disso não modifica a senha já gravada.

## Rede

O compose principal publica somente `8080`. FastAPI, PostgreSQL e Ollama ficam
na rede interna `backend`. Para depurar localmente as portas da API e do
Ollama, use explicitamente:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

O override abre as portas apenas em `127.0.0.1` e não deve ser usado em
máquinas compartilhadas.

## Login por API

O campo OAuth2 `username` usa o formato `tenant/usuario`:

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/api/auth/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=local/admin&password=SUA_SENHA' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:8080/api/auth/me \
  -H "Authorization: Bearer $TOKEN"
```

O access token expira em 15 minutos por padrão e inclui `sub`, `tenant_id`,
`role`, `scopes`, `ver`, `iss`, `aud`, `iat`, `nbf`, `exp` e `jti`.

## Sessão: access token curto + refresh rotativo

O login devolve duas coisas:

1. **Access token** (JWT, 15 min) no corpo da resposta. O navegador guarda
   apenas em memória — nunca em `localStorage` ou `sessionStorage`, que são
   legíveis por qualquer script injetado na página.
2. **Refresh token** (7 dias) em cookie `HttpOnly`, `SameSite=Strict`, restrito
   ao caminho `/api/auth`. O JavaScript não consegue lê-lo.

`POST /api/auth/refresh` troca o refresh por um par novo. A **rotação é
obrigatória**: cada uso invalida o token anterior. Se um token já rotacionado
reaparecer, o servidor conclui que houve cópia e revoga a *família* inteira —
tanto o ladrão quanto o usuário legítimo caem, e o incidente fica registrado na
auditoria como `auth.refresh / reuse_detected`.

Como o refresh se autentica por cookie, ele exige proteção anti-CSRF por
double-submit: o cabeçalho `X-CSRF-Token` precisa repetir o valor do cookie
`rag_csrf`. Um site de terceiros consegue fazer o navegador enviar o cookie,
mas não consegue lê-lo para montar o cabeçalho. As demais rotas usam `Bearer` e
por isso são imunes a CSRF por construção.

| Rota | Efeito |
|---|---|
| `POST /api/auth/refresh` | Rotaciona a sessão atual |
| `POST /api/auth/logout` | Revoga a família desta sessão |
| `POST /api/auth/logout-all` | Derruba todas as sessões do usuário |
| `POST /api/auth/password` | Troca a senha e encerra todas as sessões |

O claim `ver` acompanha `users.token_version`. Trocar a senha, desativar a conta
ou usar `logout-all` incrementa esse contador e invalida na hora todo access
token ainda dentro da validade, sem precisar de denylist de JWT.

## Proteção do login

- **Rate limit** por IP (`LOGIN_RATE_LIMIT`, 5/min) no backend e outro na borda
  do nginx (12 r/m).
- **Bloqueio temporário** da conta após `LOGIN_MAX_FAILED_ATTEMPTS` falhas
  seguidas, por `LOGIN_LOCKOUT_MINUTES`.
- **Sem enumeração de usuários**: usuário inexistente, senha errada e conta
  bloqueada devolvem exatamente a mesma resposta. Quando o usuário não existe, o
  servidor ainda executa uma verificação Argon2 descartável para que o tempo de
  resposta não denuncie quais logins são válidos.
- **Política de senha**: mínimo de 12 caracteres com maiúscula, minúscula,
  número e símbolo; não pode conter o nome de usuário nem termos de uma lista de
  senhas óbvias.

Cada tentativa recusada vira um evento de auditoria (`bad_password`, `locked`,
`locked_out`, `unknown_user`), visível na tela **Auditoria**.

## Permissões

| Role | Permissões principais |
|---|---|
| `user` | Consultar RAG e listar documentos do próprio tenant |
| `admin` | Todas as anteriores, upload, exclusão, auditoria, status e criação de usuários |

Um administrador pode criar um usuário no próprio tenant:

```bash
curl -s -X POST http://localhost:8080/api/admin/users \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"username":"analista","password":"Trilha#Vetor2026","role":"user"}'
```

`GET /api/admin/users` lista as contas do tenant e
`PATCH /api/admin/users/{id}` ativa ou desativa uma delas. Desativar revoga os
refresh tokens e incrementa o `token_version`, encerrando as sessões abertas
imediatamente. Um administrador não consegue desativar a própria conta, para
não deixar o tenant sem quem o administre.

## Camadas de defesa no front

A interface é uma SPA Angular com rotas guardadas:

| Rota | Guard | Scope exigido |
|---|---|---|
| `/login` | `guestGuard` | — (redireciona quem já tem sessão) |
| `/consulta` | `authGuard` | sessão válida |
| `/usuarios` | `authGuard` + `scopeGuard` | `admin:manage` |
| `/auditoria` | `authGuard` + `scopeGuard` | `audit:read` |

Todas as telas autenticadas ficam dentro de um shell que exige `authGuard` na
entrada e em cada navegação filha (`canActivateChild`).

Os guards decidem apenas **o que a interface mostra**. Cada rota é verificada de
novo no backend por JWT e scope: digitar a URL na mão, adulterar o token no
navegador ou chamar a API direto não dá acesso a nada. O guard existe para a
experiência do usuário; a autorização real é sempre do servidor.

A sessão também encerra sozinha após `SESSION_IDLE_MINUTES` sem interação, e o
parâmetro `returnUrl` do login só aceita caminhos internos — senão um link
`/login?returnUrl=https://site-falso` levaria o usuário recém-autenticado para
fora da aplicação.

## Isolamento de organizações

O `tenant_id` nunca vem do corpo da requisição; ele é obtido exclusivamente do
JWT validado. Ingestão, consulta, listagem e exclusão passam o tenant ao
`VectorStore`. Os IDs têm o formato:

```text
tenant_id:document_hash:chunk_number
```

O ChromaDB aplica filtro obrigatório por tenant e o BM25 mantém um índice em
memória separado para cada organização.

## Auditoria

Ações de login, criação de usuário, upload, exclusão e consulta geram eventos
append-only em `audit_events`. Consultas registram a pergunta exata, fontes
recuperadas, fontes citadas, resultado de grounding, usuário, tenant, IP,
request ID e duração. Se o banco de auditoria estiver indisponível, novas
operações rastreáveis falham fechadas com `audit_unavailable`.

Defina formalmente uma política de retenção antes de aceitar dados pessoais.
O endpoint `GET /api/audit` exige `audit:read` e nunca retorna eventos de outro
tenant.

## Cabeçalhos e limites HTTP

Toda resposta sai com `Content-Security-Policy` (sem `frame-ancestors`, sem
`object-src`, scripts só de `'self'`), `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`
restritiva e `Cross-Origin-Opener-Policy`. Respostas de `/api/` também levam
`Cache-Control: no-store`. Com `COOKIE_SECURE=true` ou `APP_ENV=production`
entra ainda o `Strict-Transport-Security`.

Os cabeçalhos são aplicados no FastAPI (respostas da API) e no nginx (arquivos
estáticos da SPA), sem duplicação: um `X-Frame-Options` repetido e conflitante
pode ser simplesmente ignorado pelo navegador.

Outros controles de borda:

- **Rate limit global** (`DEFAULT_RATE_LIMIT`, 120/min) em todas as rotas, além
  dos limites específicos de login, chat e upload. Autenticado, o limite conta
  por identidade; anônimo, por IP.
- **Limite de corpo**: `MAX_JSON_BODY_KB` (256 KB) fora do upload. Conta os
  bytes realmente recebidos, e não só o `Content-Length` — senão bastaria usar
  `Transfer-Encoding: chunked` para escapar.
- **IP confiável**: `X-Real-IP` e `X-Forwarded-For` só são aceitos de redes em
  `TRUSTED_PROXY_CIDRS`. Sem isso qualquer cliente zeraria o rate limit por IP
  trocando o cabeçalho a cada requisição.
- **`TrustedHostMiddleware`** quando `ALLOWED_HOSTS` é explícito.
- **Swagger/ReDoc/OpenAPI desativados em produção** (`ENABLE_API_DOCS`): o
  schema entrega o mapa completo de rotas e scopes a quem nem se autenticou.

## Limites ainda existentes

- Os volumes locais não possuem criptografia própria. Use disco cifrado
  (BitLocker/LUKS) ou EBS/RDS com KMS.
- TLS deve terminar em Nginx externo, ALB ou outro proxy confiável. Enquanto
  não houver TLS, `COOKIE_SECURE` fica `false` e o cookie de refresh é
  interceptável na rede.
- A validação de upload verifica extensão e assinatura básica; ainda não há
  antivírus.
- ChromaDB embutido e o rate limit `memory://` pressupõem **um único worker**.
  Com mais de um processo cada um conta sua própria cota e o limite efetivo
  vira N vezes maior — nesse caso aponte `RATE_LIMIT_STORAGE_URI` para um Redis.
- Não há MFA nem expiração periódica de senha.
- Os refresh tokens revogados e expirados permanecem na tabela; falta uma
  rotina de limpeza.
- O front não tem suíte de testes automatizados: os guards são cobertos apenas
  pela verificação equivalente no backend (`tests/test_security.py`).
- A aplicação continua exigindo revisão humana de toda resposta regulatória.
