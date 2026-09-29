# Guia de estudo: configuração consistente

Este guia explica o erro que aconteceu neste projeto e como evitar que ele se repita ao renomear o app, mudar parâmetros ou trocar o domínio (ex.: de saúde mental para regulatório farmacêutico).

---

## 1. O princípio de ouro

> **Uma configuração deve ter uma única fonte da verdade e ser copiada igual em todos os pontos que a usam.**

Se você muda `APP_NAME` em um arquivo e esquece os outros, o sistema “parece” atualizado na tela, mas continua antigo no Docker, no `.env` local ou no Swagger.

---

## 2. Mapa das configurações deste projeto

| Variável / valor | Onde deve aparecer |
|---|---|
| `APP_NAME` | `.env`, `.env.example`, `app/config.py` (default), `docker-compose.yml` (serviço `api`), frontend (`index.html`, `app.component.html`) |
| `COLLECTION_NAME` | `.env`, `.env.example`, `app/config.py`, `docker-compose.yml` |
| `RETRIEVAL_CANDIDATES`, `MAX_CONTEXT_CHUNKS` | `.env`, `.env.example`, `app/config.py`, `docker-compose.yml`, `README.md` |
| `MIN_RELEVANCE_SCORE` | `.env`, `.env.example`, `app/config.py`, `docker-compose.yml`, `README.md` |
| `CHUNK_CHARS`, `CHUNK_OVERLAP_CHARS` | `.env`, `.env.example`, `app/config.py`, `docker-compose.yml`, `README.md` |
| Modelos Ollama | `.env`, `.env.example`, `app/config.py`, `docker-compose.yml`, `README.md` |
| Domínio do produto (textos) | `safety.py`, `README.md`, `knowledge_base/`, frontend HTML, testes |

Checklist mental: **env → defaults Python → Docker → frontend → docs → testes**.

> **Atualização (v2).** `TOP_K` deixou de existir: a recuperação agora é
> híbrida e usa dois parâmetros separados, `RETRIEVAL_CANDIDATES` (quantos
> candidatos cada perna da busca traz) e `MAX_CONTEXT_CHUNKS` (quantos chegam
> ao prompt). `CHUNK_SIZE`/`CHUNK_OVERLAP` viraram
> `CHUNK_CHARS`/`CHUNK_OVERLAP_CHARS`, com a unidade explícita no nome —
> justamente para não repetir o erro de tratar caractere como token.

---

## 3. Erros que aconteceram (e o que estudar)

### 3.1 Nome inconsistente (`APP_NAME`)

Valores diferentes existiam ao mesmo tempo:

- `RAG Regulatório Farmacêutico` (correto)
- `RAG Regulatória Farmacêutica` (gênero errado)
- `RAG Saúde Local` (nome antigo no `.env`)

**Lição:** ao renomear, busque o nome antigo em todo o repositório (`Ctrl+Shift+F` / ripgrep) e troque **todos** os lugares na mesma sessão.

### 3.2 Bloco `environment` no lugar errado do Docker Compose

No YAML do Compose, cada chave tem indentação semântica. Um bloco assim:

```yaml
volumes:
  ollama_data:
  rag_data:

  environment:   # ERRADO: ficou “dentro” de volumes
    APP_NAME: ...
```

não configura o serviço `api`. As variáveis precisam estar assim:

```yaml
services:
  api:
    environment:
      APP_NAME: RAG Regulatório Farmacêutico
      COLLECTION_NAME: assuntos_regulatorios_farmaceuticos
```

**Lição:** no Docker Compose, indentação errada = configuração ignorada ou arquivo inválido. Sempre valide com:

```bash
docker compose config
```

### 3.3 Defaults do Python diferentes do `.env.example`

Exemplo do erro:

- `.env.example`: `TOP_K=6`, `MIN_RELEVANCE_SCORE=0.35`
- `config.py`: defaults `5` e `0.20`

Se alguém rodar sem `.env`, a API usa os defaults do Python — não o exemplo.

**Lição:** `.env.example` e `default=` em `config.py` devem ser idênticos.

### 3.4 `COLLECTION_NAME` truncado vs completo

- `assuntos_regulato_farmaceuticos` (cortado)
- `assuntos_regulatorios_farmaceuticos` (completo)

Mudar o nome da coleção no ChromaDB sem limpar `data/chroma` pode fazer a API “não ver” documentos antigos (outra coleção vazia).

**Lição:** coleção é identidade do índice. Se mudar o nome:

1. alinhe em todos os arquivos;
2. apague `data/chroma` (ou aceite começar índice novo);
3. reindexe os documentos.

### 3.5 Código e testes fora de sincronia com o domínio

Ao migrar de saúde mental para regulatório:

- `safety.py` perdeu `is_crisis_message`
- `schemas.py` removeu `emergency`
- mas os testes ainda esperavam crise/SAMU 192
- `rag_service.answer()` ficou incompleto (retornava resposta vazia)

**Lição:** mudança de domínio = atualizar **código + schemas + frontend + testes + README + knowledge_base** juntos.

---

## 4. Fluxo seguro para alterar configuração

Use este roteiro sempre que mudar nome, parâmetros ou domínio:

1. **Defina o valor canônico** (ex.: `APP_NAME=RAG Regulatório Farmacêutico`).
2. **Atualize `.env.example`** (template oficial do projeto).
3. **Atualize `.env` local** (o que a sua máquina realmente usa).
4. **Atualize defaults em `app/config.py`**.
5. **Atualize `docker-compose.yml` no serviço correto (`api`)**.
6. **Atualize UI** (`index.html`, textos do componente).
7. **Atualize docs** (`README.md`, `SECURITY.md` se necessário).
8. **Atualize testes** para refletir o novo comportamento.
9. **Valide**:

```bash
docker compose config
pytest
cd frontend && npm run build
```

10. **Busque resíduos do nome antigo** no projeto.

---

## 5. Como o backend lê configuração

Ordem prática neste projeto:

1. Variáveis de ambiente do sistema / Docker
2. Arquivo `.env` (carregado por `python-dotenv` em `config.py`)
3. Default hardcoded em `Settings`

Por isso:

- Docker pode sobrescrever o `.env` do host;
- se não existir `.env`, valem os defaults de `config.py`;
- o frontend **não** lê `APP_NAME` do backend automaticamente — o título da UI precisa ser atualizado manualmente.

---

## 6. Diferença entre branding e configuração técnica

| Tipo | Exemplos | Onde vive |
|---|---|---|
| Branding | nome do produto na tela, título HTML | frontend |
| Config runtime | `TOP_K`, URL do Ollama, coleção | `.env` / Docker / `config.py` |
| Regras de domínio | prompt regulatório, disclaimer | `safety.py` |
| Contrato da API | campos de `ChatResponse` | `schemas.py` + `models.ts` |

Mudar só o branding sem alinhar o contrato da API gera bugs silenciosos (campo `emergency` no front, ausente no backend, etc.).

---

## 7. Mini checklist de revisão (copie e use)

Antes de considerar a mudança pronta:

- [ ] `.env` e `.env.example` iguais nos nomes/valores relevantes
- [ ] defaults de `app/config.py` iguais ao `.env.example`
- [ ] `docker-compose.yml` com `environment` dentro de `services.api`
- [ ] `docker compose config` passa sem erro
- [ ] frontend mostra o mesmo `APP_NAME`
- [ ] `README.md` descreve os mesmos padrões
- [ ] testes passam (`pytest`)
- [ ] busca pelo nome antigo não encontra resíduos relevantes
- [ ] se `COLLECTION_NAME` mudou, índice Chroma foi recriado

---

## 8. Exercícios rápidos para fixar

1. Se você mudar `APP_NAME` para `RAG Compliance Farma`, liste todos os arquivos que precisa editar.
2. Explique o que acontece se `COLLECTION_NAME` no Docker for diferente do `.env` local.
3. Por que `TOP_K` no `.env.example` diferente do default em `config.py` é um bug de documentação?
4. O que `docker compose config` ajuda a detectar que um `print` no código não detecta?

---

## 9. Valores canônicos atuais deste projeto

Use estes valores como referência:

```env
APP_NAME=RAG Regulatório Farmacêutico
COLLECTION_NAME=assuntos_regulatorios_farmaceuticos
TOP_K=6
MIN_RELEVANCE_SCORE=0.35
OLLAMA_CHAT_MODEL=qwen2.5:3b
OLLAMA_EMBEDDING_MODEL=bge-m3
MAX_UPLOAD_MB=15
```

Se um dia mudar qualquer um deles, volte à seção 4 e execute o roteiro completo.
