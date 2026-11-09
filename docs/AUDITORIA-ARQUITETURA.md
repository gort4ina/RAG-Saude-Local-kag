# Auditoria de arquitetura: RAG Saúde Local (KAG)

Data: 2026-11-08 · Escopo: leitura estática do código em `main` (commit `a7d235c`) · Nada foi executado nem medido por mim, exceto onde indicado.

**Como ler os rótulos**
- **[código]** verificado lendo o arquivo citado.
- **[medido]** número que já está no seu README (medido por você na GTX 1650).
- **[estimado]** número de fonte externa ou cálculo meu. Trate como hipótese e meça.

---

## 1. Resumo executivo

O projeto está **bem acima da média para um protótipo**: Argon2 com tempo constante, refresh token rotativo com detecção de reuso, CSRF double-submit, RLS no Postgres, guardas de produção no `Settings`, recusa quando não há evidência, auditoria de citações, métricas Prometheus. A fundação de segurança de aplicação é boa.

Os problemas que mais importam não estão nas rotas HTTP. Estão em três lugares:

1. **A camada de dados é process-local** (Chroma embutido + BM25 em memória + rate limit em memória + semáforo). Isso impede escalar e impede deploy sem downtime. O backend `pgvector` que deveria resolver isso **ainda não é pgvector** (item E2).
2. **A qualidade da recuperação tem falhas de desenho** que o threshold `0.45` mascara: candidatos só-BM25 são descartados por terem score vetorial 0 (item L11), e o orçamento de tokens do prompt nunca é medido (item L2).
3. **O que diz "segurança" é mais forte no papel do que na configuração padrão**: o compose assume `production`, mas o `.env.example` sobe `development`, então os guardas de produção nunca disparam (item S2).

### Top 10 ações, por retorno sobre esforço

| # | Ação | Tema | Esforço |
|---|------|------|---------|
| 1 | Trocar as 3 senhas que estiveram no `.env copy` público e limpar o histórico | Segurança | 1 h |
| 2 | Criar `.env.production.example` e um perfil de compose que realmente liga os guardas de produção | Segurança | 2 h |
| 3 | Corrigir `passes_relevance` para candidatos só-BM25 (calcular o cosseno deles) | Qualidade | 2 h |
| 4 | Medir tokens do prompt (`prompt_eval_count`) e aplicar orçamento de contexto | LLM | 3 h |
| 5 | Aquecer os modelos no startup e tornar `keep_alive` configurável (hoje fixo em `"10m"`) | Performance | 1 h |
| 6 | Trocar `count_chunks` (varre todos os ids) por `collection.count()` com cache | Performance | 1 h |
| 7 | Corrigir possível vazamento do `InferenceGate` no streaming | Robustez | 1 h |
| 8 | Aplicar prefixos do EmbeddingGemma (consulta vs. documento) e medir o ganho no eval | LLM | 3 h |
| 9 | Rodar o `eval/run_eval.py` de verdade, guardar `results.json` e ligar no CI | Qualidade | 4 h |
| 10 | Completar o pgvector de verdade (HNSW + FTS do Postgres) e aposentar Chroma + BM25 em memória | Escala | 1–2 semanas |

---

## 2. Segurança

**Pontos fortes confirmados [código]:** login com tempo constante (`burn_password_time`), bloqueio por tentativas, refresh rotativo com revogação de família (`main.py:471`), access token só em memória no front, `token_version` para invalidar tokens, RBAC por scope, RLS por `set_config`, CSP restritiva, limite de corpo que conta bytes reais (não confia só em `Content-Length`), minimização LGPD da pergunta (hash por padrão), cota de upload por tenant, `/api/metrics` protegido.

| ID | Sev. | Achado | Evidência | Ação |
|----|------|--------|-----------|------|
| S1 | **Alta** | `.env copy` com `POSTGRES_PASSWORD`, `JWT_SECRET` e `BOOTSTRAP_ADMIN_PASSWORD` reais esteve num repositório **público**. O commit `a7d235c` removeu o arquivo, mas o histórico ainda o contém. | git history | Rotacionar os 3 segredos (e a senha do usuário `rag` dentro do Postgres, pois a variável só vale na criação do banco). Limpar o histórico com `git filter-repo` e ativar *push protection*/*secret scanning* no GitHub. |
| S2 | **Alta** | Os guardas de produção nunca disparam na prática. O compose usa `APP_ENV=${APP_ENV:-production}` (`docker-compose.yml:101`), mas o `.env.example` define `development`. Com `development`: Swagger aberto, `COOKIE_SECURE=false`, `ALLOWED_HOSTS=*`, sem HSTS. Se alguém subir com o padrão do compose, a API **recusa iniciar** (`config.py:408`), o que empurra todo mundo para `development`. | `config.py:383-427`, `docker-compose.yml:101,113,161` | Entregar `.env.production.example` com valores válidos e um `docker-compose.prod.yml` (TLS, `COOKIE_SECURE=true`, hosts reais). Falhar no CI se o exemplo de produção não passar no `Settings()`. |
| S3 | Média | Isolamento de tenant no Chroma é um filtro de metadados (`where tenant_id`) numa coleção única. O RLS só protege tabelas do Postgres, não os vetores. Um método novo que esqueça o `where` vaza dados entre organizações. | `vector_store.py` (todos os métodos recebem `tenant_id` manualmente) | Migrar para pgvector com RLS em `knowledge_chunks` (a policy faz o trabalho, não o desenvolvedor). Até lá, teste de propriedade: "nenhuma consulta retorna chunk de outro tenant". |
| S4 | Média | Defesa contra *prompt injection* é uma lista de regex (`safety.py:25-44`). Frases parafraseadas, outros idiomas, Unicode ou instruções partidas entre chunks passam. A defesa que realmente segura é a exigência de citação válida. | `safety.py`, `rag_service.py:550` | Manter o regex como camada barata, mas adicionar: (a) só admins sobem documentos (já é assim, ótimo), (b) varredura no upload com classificador, (c) saída estruturada com citação verificável (ver L3), (d) teste adversarial no eval. Referência: OWASP LLM01. |
| S5 | Média | A auditoria de citações só confere que `[Fonte N]` **existe**, não que a afirmação **é sustentada** pela fonte. Um modelo de 3B pode citar o número certo para uma frase errada e a resposta sai `grounded=true`. | `citations.py:126-145` | Pedir trecho literal (*quote*) por afirmação e checar por substring/NLI. Medir *faithfulness* no eval. |
| S6 | Média | Parser de PDF (`pypdf`) roda no processo da API, sem limite de páginas nem de tempo, com `mem_limit: 2g`. Um PDF malicioso ou gigante derruba a API inteira (e o chat junto). | `main.py:1000-1070`, `rag_service.py:199` | Mover a ingestão para um worker separado com timeout, limite de páginas e CPU/mem próprios (ver E5). |
| S7 | Média | Hardening de container incompleto: sem `read_only`, `cap_drop: [ALL]`, `security_opt: no-new-privileges`; imagens por tag, não por digest; `pytest` e `aiosqlite` na imagem de produção; CI sem `pip-audit`, Trivy, Dependabot nem SAST. | `docker-compose.yml`, `requirements.txt:17-18`, `ci.yml` | Separar `requirements-dev.txt`; adicionar esses controles; gerar SBOM. |
| S8 | Média | O `ollama` fica **permanentemente** na rede `edge` (com saída para a internet) só para baixar modelos no bootstrap. Um RCE no Ollama teria egress livre. | `docker-compose.yml:53` | Depois do primeiro pull, subir sem `edge` (perfil `offline`) ou pré-baixar os modelos numa imagem. |
| S9 | Baixa | `/api/health` é `@limiter.exempt` e faz uma chamada ao Ollama a cada hit. Só o nginx limita (20 req/s). | `main.py:814-824` | Cache de 2–5 s do resultado do health. |
| S10 | Baixa | Auditoria "append-only" não é à prova de adulteração (o app tem `DELETE` para a poda). Em contexto regulatório, vale ter evidência de integridade. | `audit.py` | Encadear hash (cada evento guarda o hash do anterior) ou exportar para armazenamento WORM. |
| S11 | Baixa | CSP inclui `navigate-to`, diretiva removida da especificação (navegadores ignoram e geram aviso). `style-src 'unsafe-inline'` por causa do Angular. | `security.py:47` | Remover `navigate-to`; migrar para nonces quando possível. |
| S12 | Baixa | JWT HS256 com segredo compartilhado, e o `db-migrate` recebe o `JWT_SECRET` sem precisar. | `docker-compose.yml:87` | Remover do `db-migrate`. Se algum dia houver mais de um serviço validando token, usar EdDSA/RS256. |
| S13 | Baixa | Sem TLS embutido; o `web` publica `8080` em HTTP. | `docker-compose.yml:195` | Caddy/Traefik na frente com certificado automático; só então `COOKIE_SECURE=true`. |

---

## 3. Performance

| ID | Sev. | Achado | Evidência | Ação |
|----|------|--------|-----------|------|
| P1 | **Alta** | **`count_chunks` varre a coleção inteira a cada pergunta**: `collection.get(where=..., include=[])` devolve todos os ids só para chamar `len()`. Custo O(N) por consulta, antes mesmo do embedding. | `vector_store.py:147-155` | Cache por tenant (invalidar em `upsert/delete`) ou `collection.count()` quando houver só um tenant. |
| P2 | **Alta** | **Cold start de ~100 s** [medido, README §19]: 40 s carregando o gerador + 13,6 s no primeiro embedding + geração a 7 tok/s. O `warmup()` só reconstrói o BM25; não carrega modelo nenhum. | `rag_service.py:136-147` | No `lifespan`, disparar um `/api/generate` e um `/api/embed` mínimos com `keep_alive` longo. |
| P3 | Média | `keep_alive` está **fixo em `"10m"`** no payload do chat e ignora `OLLAMA_KEEP_ALIVE`. As chamadas de embedding nem enviam. Em máquina dedicada, o ideal é `-1` (residente). | `ollama_client.py:413` | Ler de `Settings`; usar `-1` em produção. |
| P4 | Média | **Métricas de retrieval enganosas**: vetor, BM25 e grafo rodam em `asyncio.gather` e os três recebem **o mesmo** `stage_ms` (tempo total do gather). Os painéis mostram três números iguais. | `rag_service.py:393-400` | Cronometrar cada tarefa dentro da própria corrotina. |
| P5 | Média | **`_chunks_from_graph` é N+1 e pesado**: para cada documento citado pelo grafo, busca *todos* os ids de chunk e depois *todos* os textos (lotes de 100), para escolher 2 por relação. Em documento grande são centenas de leituras por pergunta. | `rag_service.py:462-534` | Gravar `source_chunk_id` na aresta do grafo na ingestão e buscar só esses ids. |
| P6 | Média | **Reindexar o BM25 inteiro a cada upload, exclusão e mudança de status** (`_refresh_bm25` lê todos os chunks do tenant). `update_document_status` também puxa e regrava os **embeddings** só para trocar um rótulo. | `rag_service.py:155-159`, `vector_store.py:380-444` | Atualização incremental do índice; para o status, atualizar só os metadados (e não embutir o status no texto do chunk). |
| P7 | Média | **Duas escritas síncronas de auditoria no caminho de cada pergunta**, cada uma abrindo sessão e transação nova. Mais uma consulta ao banco para autenticar cada requisição. | `main.py:1317-1346`, `auth.py:258` | Fila assíncrona de auditoria com *outbox* (mantendo a garantia de gravação); cache curto de principal com invalidação por `token_version`. |
| P8 | Média | **Latência dominada pelo prefill, não pela geração.** Prompt de ~3,5k tokens [estimado, ver L2] na 1650 (sem Tensor Cores) leva vários segundos antes do primeiro token. | `safety.py`, `retrieval.py` | Encurtar o system prompt, reduzir chunks/tamanho, manter prefixo estável para reuso de cache. Meça `prompt_eval_duration_ms / prompt_eval_count` (já é logado). |
| P9 | Baixa | Embeddings de ingestão em lotes sequenciais (`for` com `await`) e disputando a GPU com o chat. Como o Ollama serializa, não há ganho em paralelizar, mas **uma ingestão grande bloqueia o chat**. | `rag_service.py:239-243` | Ver E5 (worker e prioridade). |
| P10 | Baixa | `mem_limit: 2g` na API com Chroma + BM25 (dicionários Python) + pypdf. Com algumas centenas de milhares de chunks, o BM25 em memória estoura. | `docker-compose.yml:186` | Resolvido ao migrar a busca lexical para o Postgres (E2). |

---

## 4. Otimização (build, custo de operação, manutenção)

- **Dependências:** separar `requirements.txt` (runtime) de `requirements-dev.txt` (pytest, etc.); usar `uv pip compile --generate-hashes` ou `pip-tools` para travar hashes. Reduz imagem e superfície de ataque.
- **Imagem do backend:** `python:3.12-slim` está bom; vale *multi-stage* se alguma dependência precisar compilar, e remover `COPY knowledge_base` da imagem de produção.
- **CI:** hoje só roda `pytest` e `compileall`. Adicionar `ruff`, `mypy` (ou `pyright`), `pip-audit`, `trivy image`, `hadolint`, `npm audit`, e **o eval offline como gate** (falha se *Recall@5* ou *abstenção correta* cair).
- **Compose:** usar `profiles` (`cpu`, `gpu`, `prod`) em vez de arquivos de override soltos; ler `OLLAMA_*` de um único lugar.
- **Config duplicada:** `chunk_chars` tem padrão `1000` em `RagOptions` e `1900` em `Settings`; qualquer teste que use o padrão errado mede outra coisa. Fonte única de verdade.
- **README desatualizado:** o §27 diz que `expected_document` vem vazio, mas o `eval/dataset.json` já os traz preenchidos (43 ocorrências). E não há `eval/results.json` versionado, então **não existe baseline numérico**.

---

## 5. Escalabilidade

| ID | Sev. | Achado | Evidência | Ação |
|----|------|--------|-----------|------|
| E1 | **Alta** | **Tudo é por processo**: Chroma embutido (SQLite), BM25 em memória, rate limit `memory://`, semáforo. Em produção a app aborta se `WEB_CONCURRENCY > 1`. Resultado: um só processo, sem alta disponibilidade e **deploy com indisponibilidade** (cada restart reconstrói o BM25 e esfria os modelos). O código reconhece isso. | `main.py:139-179` | Estado em serviços externos: vetores+FTS no Postgres, rate limit no Redis, fila de inferência compartilhada. |
| E2 | **Alta** | **O backend `pgvector` ainda não é pgvector.** Mantém todos os vetores em um dict Python (`_memory`), calcula cosseno em Python puro (O(N·d) por consulta), `health()` devolve sempre `True`, e as funções que gravam/hidratam do SQL (`persist_chunk_rows`, `hydrate_pgvector_store`) **não são chamadas por nenhum código** (nem por `app/`, nem por testes). Com `VECTOR_BACKEND=pgvector`, **tudo se perde ao reiniciar**. | `pgvector_store.py:63,77-102,246-292` | Implementar de verdade: coluna `vector(768)`, índice HNSW, consulta `ORDER BY embedding <=> $1`, RLS. Até lá, não usar essa opção e deixar isso explícito no `.env.example`. |
| E3 | Média | **Admissão de inferência sem fila**: concorrência 1 e espera de 0,10 s. O segundo usuário simultâneo leva `429` imediato mesmo que a primeira resposta termine em 5 s. | `inference_gate.py`, `config.py:243-248` | Fila limitada com espera de dezenas de segundos, evento de streaming `queued` com posição estimada, prioridade (chat > ingestão). |
| E4 | Média | **Ingestão síncrona dentro do request HTTP** (extrai, embeda, grava, sincroniza grafo, reindexa BM25), com `5/hora` de rate limit para esconder o custo. | `main.py:1068-1092` | `POST /documents` responde `202 + job_id`; worker lê de uma fila (Postgres `SKIP LOCKED`, ARQ ou RQ); status por polling/SSE. |
| E5 | Média | **Três lugares de persistência** (Postgres, volume do Chroma, uploads) e o backup do README copia o volume do Chroma "a quente" (SQLite em escrita pode sair corrompido). | README §24 | Com tudo no Postgres, `pg_dump`/PITR resolve de forma consistente. |
| E6 | Baixa | BM25 por tenant em memória: memória cresce com `tenants × chunks`. | `rag_service.py:126` | Resolvido por FTS no Postgres. |
| E7 | Baixa | Sem tracing distribuído (há `tracing.span`, mas não OpenTelemetry) e sem dashboards/alertas versionados. | `app/services/tracing.py` | OTel + Grafana (painéis e alertas em arquivo). |

---

## 6. Otimização de LLM

| ID | Sev. | Achado | Evidência | Ação |
|----|------|--------|-----------|------|
| L1 | Média | **Modelo nunca foi comparado com alternativas no seu eval.** `qwen2.5:3b` é uma boa escolha para 4 GB, mas há concorrentes na mesma faixa (Qwen3-4B Instruct, Gemma 3 4B, Phi-4-mini, Llama 3.2 3B). Nenhuma recomendação minha vale sem rodar o seu conjunto de perguntas. | `.env.example:80` | Rodar `eval/sweep.py` com 3–4 modelos e escolher por *faithfulness* + latência. |
| L2 | **Alta** | **Orçamento de tokens nunca é medido.** System prompt (~2,9 mil caracteres ≈ 750 tokens) + 4 chunks de ~1900 caracteres com cabeçalho (≈ 2,3 mil tokens) + relações KAG + pergunta + `num_predict=450` encosta no `num_ctx=4096` [estimado]. Estourou? O Ollama pode truncar o prompt e só avisa no log do servidor. `count_tokens()` existe e **não é usada**. | `ollama_client.py:330`, `safety.py:67-124`, `rag_service.py:448-460` | Medir `prompt_eval_count` em toda resposta, alertar quando `prompt_eval_count + num_predict > 0,9 × num_ctx`, e montar o contexto por orçamento (corta o chunk menos relevante em vez de deixar o Ollama cortar). |
| L3 | Média | **Citação por regex em texto livre.** Se o 3B escrever `[Fonte1]` ou `[Fonte 1,2]` fora do padrão, a resposta boa é descartada e vira "não encontrei" (`_final_answer`). | `citations.py:17`, `rag_service.py:550-560` | **Saída estruturada** (Ollama `format` com JSON Schema): `{"answer": "...", "claims":[{"text":"...", "source": 2, "quote":"..."}]}`. A decodificação restrita garante o formato e permite checar `quote` contra o chunk (resolve S5 também). |
| L4 | Média | **`num_predict=450` corta a resposta no meio** (o formato pede 4 seções), e o código não verifica `done_reason == "length"`. | `ollama_client.py:414-418` | Detectar `length`, sinalizar a resposta como truncada, e ajustar o formato/limite. |
| L5 | Média | **Reranker é o próprio 3B via prompt**, com 20 candidatos × 700 caracteres (~3,7 mil tokens): não cabe em 4096 e custa uma geração inteira. Está desligado por padrão, e é melhor assim. | `reranker.py:92-121` | Cross-encoder de verdade na **CPU**: `BAAI/bge-reranker-v2-m3` (568 M, multilíngue) ou `gte-multilingual-reranker-base` (306 M), em ONNX int8. Avaliar contra a ausência de reranker no seu eval. |
| L6 | Média | **EmbeddingGemma sem prefixos de tarefa.** A documentação do modelo recomenda formatos distintos para consulta e documento; o código envia o texto cru nos dois casos [verifique no model card]. O modelo também suporta Matryoshka (768→512/256/128), útil para reduzir índice. | `embeddings.py`, `ollama_client.py:376-405` | Aplicar os prefixos recomendados e medir *Recall@5* antes/depois (exige reindexar). |
| L7 | Média | **Embedding na GPU disputa VRAM** com o gerador (0,68 GB residentes só para a pergunta). Com `OLLAMA_MAX_LOADED_MODELS=2` os dois ficam carregados. | README §19 | Rodar o embedding na CPU (`options.num_gpu: 0` na chamada `/api/embed`) libera ~0,7 GB para um gerador maior ou mais contexto. Meça a latência da pergunta na CPU. |
| L8 | Média | **Cache de resposta ausente.** Perguntas regulatórias se repetem muito ("o que é AFE?"). | — | Cache por `(tenant, hash(pergunta normalizada), versão do corpus)`, invalidado na ingestão. Cada acerto economiza 2–13 s de GPU [medido]. |
| L9 | Média | **Active Harness com retries multiplica latência**: cada retentativa é outra geração inteira (~10 s a 30 tok/s). | `settings.py`, `.env.example:198-208` | Manter *evaluation-only* até haver ganho comprovado no eval; se ligar, `HARNESS_MAX_RETRIES=1`. |
| L10 | Média | **Prompt de sistema longo para um modelo pequeno.** Dez regras numeradas + formato em quatro seções. Modelos de 3B seguem pior listas longas. | `safety.py:67-124` | Encurtar, mover o formato para o JSON Schema (L3) e adicionar 1 exemplo (*few-shot*) com `[Fonte N]`. |
| L11 | **Alta** | **Candidatos só-BM25 são descartados.** Um chunk que o BM25 achou e o vetor não tem `vector_score = 0.0` (padrão do dataclass), e `passes_relevance` exige `vector_score ≥ 0.45` ou referência normativa literal com BM25 relativo ≥ 0.5. Consultas por sigla ou termo raro sem número ("SNGPC", "RT farmácia") perdem exatamente o ganho do híbrido. | `retrieval.py:127-135,161-165` | Calcular o cosseno real dos candidatos só-BM25 (buscar o embedding no store) ou usar o score pós-fusão/reranker como critério. |
| L12 | Baixa | Threshold `0.45` ainda é um chute (o próprio `.env.example` diz). | `.env.example:126-128` | Calibrar com o eval (curva de *abstenção correta × fundamentação*). |
| L13 | Baixa | Recuperação não usa **contexto por chunk** (cada trecho "sabe" de qual norma/seção veio). O chunker já põe cabeçalho, o que ajuda. | `chunking.py` | Avaliar *contextual retrieval* (resumo curto do documento/seção anexado ao chunk antes de embedar e indexar no BM25). |

---

## 7. Como melhorar a arquitetura

### 7.1 Arquitetura-alvo

```
                 ┌──────────────── Caddy/Traefik (TLS) ────────────────┐
                 │                                                      │
            nginx (SPA)                                          /api/*
                                                                   │
                                          ┌────────────────────────▼───────────────────────┐
                                          │ API FastAPI (stateless, N réplicas)            │
                                          │  auth · RBAC · rate limit (Redis) · chat       │
                                          └───────┬───────────────┬────────────────┬───────┘
                                                  │               │                │
                                     ┌────────────▼───┐   ┌───────▼───────┐  ┌─────▼────────────┐
                                     │ Postgres        │   │ Redis          │  │ Camada de inferência │
                                     │  · usuários/RLS │   │  · rate limit  │  │  · fila + prioridade │
                                     │  · pgvector HNSW│   │  · cache resp. │  │  · cache de resposta │
                                     │  · FTS (pt)     │   │  · fila jobs   │  │  · Ollama / llama-srv│
                                     │  · grafo KAG    │   └───────┬────────┘  └──────────────────────┘
                                     │  · auditoria    │           │
                                     └────────▲────────┘   ┌───────▼─────────┐
                                              │            │ Worker de ingestão │  (container separado:
                                              └────────────│  PDF/OCR/chunk/embed│   timeout, limite de páginas,
                                                           └────────────────────┘    CPU/mem próprios)
```

**O que muda e por quê**
1. **Um único datastore para dados de conhecimento** (Postgres + pgvector + FTS): API fica *stateless*, RLS protege vetores, backup é consistente (resolve E1, E2, E5, E6, S3, P1, P6, P10).
2. **Ingestão fora do request** (resolve E4, S6, P9).
3. **Camada de inferência com fila e cache** (resolve E3, L8) e uma interface `LLMPort` para trocar Ollama por llama-server/vLLM sem mexer no resto.
4. **Qualidade guiada por eval** (L1, L5, L6, L12): nenhuma mudança de modelo/parâmetro entra sem número.

### 7.2 Roadmap sugerido

| Fase | Entrega | Duração |
|------|---------|---------|
| 0 | Segredos, perfil de produção (S1, S2), `count_chunks` (P1), warm-up e `keep_alive` (P2, P3), correção do `InferenceGate` (§7.3), métricas por etapa (P4) | 1–2 dias |
| 1 | Medir e corrigir qualidade: L2, L11, L6; rodar e versionar baseline de eval; CI com gate | ~1 semana |
| 2 | Saída estruturada + verificação de citação (L3, S5), cache de resposta (L8), orçamento de contexto | ~1 semana |
| 3 | pgvector real + FTS + RLS nos chunks (E2); remover Chroma e BM25 em memória | 1–2 semanas |
| 4 | Worker de ingestão + fila + OCR (E4, S6); múltiplas réplicas da API; Redis | 1–2 semanas |
| 5 | Hardening de container, SBOM, scan, TLS, OTel, painéis e alertas (S7–S13, E7) | ~1 semana |

### 7.3 Problema extra de robustez (verificar)

`chat_stream` chama `await gate.acquire()` **antes** de devolver o `StreamingResponse` (`main.py:1406`), mas só libera no `finally` do gerador (`main.py:1452`). Se o cliente desconectar antes de o gerador começar a rodar, o corpo do `try` nunca executa e o semáforo não é liberado. Com concorrência 1, uma única ocorrência trava o chat em `429` até reiniciar. **Correção:** adquirir dentro do gerador, ou usar um `BackgroundTask` que sempre libere. Vale um teste que simule a desconexão.

---

## 8. Limites da GTX 1650 (4 GB) e onde dá para chegar

### 8.1 O que a placa permite, em números

- **VRAM:** 4096 MiB. No seu README [medido]: com `qwen2.5:3b` + `embeddinggemma` carregados, `nvidia-smi` mostra **3084 MiB** usados → **~1 GiB de folga**.
- **KV cache é barato no Qwen2.5-3B:** o log do seu Ollama mostra **76,5 MiB para 4096 tokens em q8_0** (`36 camadas, 2 cabeças KV`). Isso é **~19 KiB por token** em q8_0. Dobrar o contexto para 8192 custa só **+~76 MiB de KV** [calculado a partir do seu log]; o buffer de computação cresce um pouco mais. O aviso do README ("não suba `OLLAMA_CONTEXT_LENGTH` acima de 4096 sem medir") é correto na parte do "sem medir", mas **provavelmente há margem** para 6–8 K com este modelo.
- **Velocidade de geração (largura de banda):** a 1650 tem ~128 GB/s (GDDR5; ~192 GB/s na variante GDDR6). Geração é limitada por leitura dos pesos, então o teto teórico é `banda ÷ tamanho do modelo`:

  | Modelo (Q4_K_M) | Pesos | Teto teórico | Medido / estimado |
  |-----------------|-------|--------------|-------------------|
  | qwen2.5:3b | ~1,9 GB | ~67 tok/s | **29–30 tok/s [medido]** (~45% do teto) |
  | Qwen3-4B | ~2,5 GB | ~51 tok/s | 18–24 tok/s [estimado, fonte 1] |
  | 7B | ~4,0–4,4 GB | não cabe | 4–10 tok/s com offload parcial [estimado] |

  Seu número real (29–30) cai dentro da estimativa da fonte para modelos de 3B (25–32), então as outras estimativas da mesma tabela são razoáveis como ordem de grandeza.
- **Prefill (ler o prompt) é limitado por cálculo**, não por banda, e a placa não tem Tensor Cores. Com prompts de ~3–3,5 mil tokens, **o tempo até o primeiro token tende a pesar mais que a geração**. Você já loga `prompt_eval_count` e `prompt_eval_duration_ms`: é a medição mais importante que falta.

### 8.2 Perfis com resultados esperados

> Só o perfil A tem números medidos por você. Os demais são hipóteses para você validar com o procedimento da §8.3.

| Perfil | Configuração | VRAM aprox. | Geração | Observação |
|--------|--------------|-------------|---------|------------|
| **A. Atual** | `qwen2.5:3b` + `embeddinggemma` na GPU, ctx 4096 | ~3,1 GB **[medido]** | **29–30 tok/s [medido]**; 2,1–13,3 s por resposta (quente), ~100 s (frio) | Frio é o ponto fraco: resolva com warm-up (P2). |
| **B. Mais evidência** | Mesmos modelos, `OLLAMA_CONTEXT_LENGTH=8192`, `MAX_CONTEXT_CHUNKS=6` | ~3,3–3,5 GB [estimado] | ~29 tok/s, mas **prefill ~1,5–2× maior** | Mais contexto custa tempo até o primeiro token. Só vale se o eval mostrar ganho. |
| **C. Modelo maior** | `Qwen3-4B-Instruct` Q4_K_M + embedding na **CPU** (L7), ctx 4096, KV q8_0 | ~3,3–3,6 GB [estimado] | 18–24 tok/s [estimado, fonte 1] | Margem curta. Meu cálculo pela arquitetura (36 camadas, 8 cabeças KV, dim 128) dá ~300 MB de KV em q8_0 a 4096 tokens; a fonte cita ~90 MB (q8) e as duas divergem, então **meça**. Com o display em outra GPU (iGPU de notebook), ganha 200–500 MB. |
| **D. Tarefas leves** | `Qwen3-1.7B` ou `Llama 3.2 1B` para reescrita de consulta/roteamento | ~1,1 GB / ~0,8 GB | 33–42 tok/s [estimado, fonte 1] | Trocar de modelo na GPU custa 1–3 s de carga; não deixe os dois residentes. |
| **E. 7B** | Qualquer 7B Q4_K_M | ~4,0 GB só de pesos | 4–10 tok/s com offload [estimado] | **Não recomendado**: não cabe sem ir para a CPU, e Q2_K "cabe" mas a qualidade é inutilizável (fonte 1). |

**Conclusão prática:** o teto realista da 1650 para este projeto é **um modelo de 3–4 B em Q4 com contexto de 4–8 K, uma pergunta por vez**, a ~20–30 tok/s. O que mais vai melhorar a experiência **não é trocar de modelo**, e sim: (1) eliminar o cold start, (2) reduzir tokens de prompt, (3) cache de respostas, (4) recuperação melhor (L11, L6, L5). Para 7–8 B ou mais de um usuário simultâneo é preciso outra GPU (a fonte 1 cita uma RTX 3060 12 GB usada na faixa de US$ 200–400 como o salto mais barato).

### 8.3 Como medir você mesmo (30 minutos)

```bash
# 1. Velocidade de geração e prefill, isolados da aplicação
docker exec -it rag-ollama ollama run qwen2.5:3b --verbose "Explique AFE em 3 frases."

# 2. O que está na VRAM e quanto está na GPU
docker exec rag-ollama ollama ps
nvidia-smi --query-gpu=memory.used,memory.total --format=csv

# 3. Tokens reais do prompt e tempo de prefill nas suas perguntas (já logados)
docker compose logs api | grep chat_completed
#   olhe: prompt_eval_count, prompt_eval_duration_ms, eval_count, tokens_per_second, load_duration_ms
```

Para comparar perfis de forma justa, use o `eval/sweep.py` (variando `OLLAMA_CHAT_MODEL`, `OLLAMA_CONTEXT_LENGTH`, `MAX_CONTEXT_CHUNKS`) e registre, por perfil: *Recall@5*, taxa de fundamentação, abstenção correta, p50/p95 de latência e `prompt_eval_count` médio. Um teste isolado com `llama-bench` (do llama.cpp) dá o teto de prefill e geração da placa, sem a aplicação.

---

## 9. Referências de leitura

### Segurança
- OWASP, **Top 10 for LLM Applications** (injeção de prompt, saída insegura, vazamento): https://genai.owasp.org/llm-top-10/
- OWASP, **Application Security Verification Standard (ASVS)**: https://owasp.org/www-project-application-security-verification-standard/
- OWASP **Cheat Sheet Series** (JWT, CSRF, Docker, File Upload): https://cheatsheetseries.owasp.org/
- NIST, **AI Risk Management Framework**: https://www.nist.gov/itl/ai-risk-management-framework
- **CIS Docker Benchmark**: https://www.cisecurity.org/benchmark/docker
- **LGPD** (Lei 13.709/2018): http://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm

### Arquitetura e escala
- **The Twelve-Factor App** (processos stateless, config, logs): https://12factor.net/
- **pgvector** (HNSW, IVFFlat, operadores): https://github.com/pgvector/pgvector
- Kleppmann, *Designing Data-Intensive Applications* (livro) para filas, consistência e backup.

### RAG, recuperação e avaliação
- Lewis et al., **Retrieval-Augmented Generation** (artigo original): https://arxiv.org/abs/2005.11401
- Anthropic, **Contextual Retrieval** (contexto por chunk + BM25 + reranking): https://www.anthropic.com/news/contextual-retrieval
- Cormack et al., **Reciprocal Rank Fusion**: https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf
- Liu et al., **Lost in the Middle** (posição do contexto no prompt): https://arxiv.org/abs/2307.03172
- Gao et al., **HyDE**: https://arxiv.org/abs/2212.10496
- Asai et al., **Self-RAG**: https://arxiv.org/abs/2310.11511 · Yan et al., **Corrective RAG**: https://arxiv.org/abs/2401.15884
- Es et al., **RAGAS** (métricas de RAG): https://arxiv.org/abs/2309.15217
- Husain, **Your AI Product Needs Evals**: https://hamel.dev/blog/posts/evals/
- Yan, **Patterns for Building LLM-based Systems**: https://eugeneyan.com/writing/llm-patterns/

### Grafos de conhecimento (KAG)
- Edge et al., **GraphRAG** (Microsoft): https://arxiv.org/abs/2404.16130
- Liang et al., **KAG** (Ant Group): https://arxiv.org/abs/2409.13731

### Embeddings e reranking
- Google, **EmbeddingGemma** (model card e prompts recomendados): https://ai.google.dev/gemma/docs/embeddinggemma
- Chen et al., **BGE M3-Embedding**: https://arxiv.org/abs/2402.03216
- Kusupati et al., **Matryoshka Representation Learning**: https://arxiv.org/abs/2205.13147
- Estudo brasileiro sobre reranking multilíngue vs. específico para português (JBCS; li só o resumo): https://journals-sol.sbc.org.br/index.php/jbcs/article/download/5659/3983/39626

### Inferência e GPU pequena
- **Ollama FAQ** (concorrência, `keep_alive`, KV cache, flash attention): https://github.com/ollama/ollama/blob/main/docs/faq.md
- **Ollama, Structured Outputs**: https://ollama.com/blog/structured-outputs
- **llama.cpp** (inclui `llama-bench` e `llama-server --parallel`): https://github.com/ggml-org/llama.cpp
- Dao et al., **FlashAttention**: https://arxiv.org/abs/2205.14135 · Kwon et al., **PagedAttention/vLLM**: https://arxiv.org/abs/2309.06180
- InsiderLLM, **What Can You Actually Run on 4GB VRAM?** (fonte 1; tabela de modelos e velocidades estimadas): https://insiderllm.com/guides/what-can-you-run-4gb-vram/
- Towards AI, **Local LLMs up to 2.24× faster on a 4GB NVIDIA GPU** (GTX 1650 Ti, posicionamento de camadas e KV; não consegui abrir a página, só o resumo da busca): https://pub.towardsai.net/how-i-made-local-llms-up-to-2-24-faster-on-a-4gb-nvidia-gpu-e0c02f2b1821

---

## 10. Limites desta auditoria

- Li o backend principal, a configuração, o compose, o nginx, o CI e partes do front. **Não li** `knowledge_graph.py`, `llm_extractor.py`, `ocr.py`, `document_loader.py`, `text_splitter.py` (só trechos), `metadata.py`, o front inteiro, as migrações Alembic nem os testes. Podem esconder outros achados.
- **Não rodei nada**: testes, eval, ataques, benchmarks. Todo achado marcado [código] é leitura estática.
- As buscas web trouxeram em sua maioria estimativas de terceiros para a GTX 1650; a única medição desta placa neste relatório é a do seu README.
- A comparação de modelos (L1) e o ganho de cada ajuste dependem do seu conjunto de perguntas. Nada aqui substitui rodar o eval.
