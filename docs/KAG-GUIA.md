# Camada KAG: guia prático

## O que é

O projeto continua uma **RAG**: o ChromaDB encontra os trechos originais e o
BM25 encontra termos exatos. A camada **KAG** grava também relações explícitas
no grafo (PostgreSQL local por padrão). Ela **não substitui** as fontes e
**não decide** a vigência por conta própria.

A resposta só é marcada como fundamentada se citar trechos recuperados
(`[Fonte N]`). Relações do grafo entram no prompt apenas como **orientação**.

## Como ligar / desligar

No `.env` (veja `.env.example`):

```env
KAG_ENABLED=true
KAG_GRAPH_BACKEND=postgres
```

| Variável | Efeito |
|---|---|
| `KAG_ENABLED=true` | Ingestão sincroniza o grafo; a consulta busca relações em paralelo |
| `KAG_ENABLED=false` | RAG puro (vetor + BM25). Endpoints `/api/knowledge/relations` devolvem lista vazia |
| `KAG_GRAPH_BACKEND=postgres` | Backend local (padrão). Sem AWS |
| `KAG_GRAPH_BACKEND=neptune` | Stub Neptune — exige `NEPTUNE_ENDPOINT`; ainda **não** executa openCypher |

Reinicie a API após mudar as variáveis (`docker compose up -d api` ou
equivalente).

## Modelo de entidades

Kinds canônicos: `documento`, `fonte`, `tema`, `norma`, `conceito`,
`evidencia`. Aliases legados (`orgao`, `assunto`, `vigencia`) continuam
válidos para dados já indexados.

Relações típicas:

- `documento → identifica → norma`
- `norma → emitida_por → fonte`
- `documento → trata_de → tema`
- `evidencia → evidencia_vem_de → documento`
- `conceito → relacionado_a → conceito` (coocorrência; confiança < 1)

Cada aresta tem `source_document_id`, `source_span`, `validation_status`
(`pendente_de_validacao` / `validada` / `rejeitada`) e proveniência
(`extracted_by`, `confidence`).

## Fluxo de uma pergunta (híbrido)

1. Ollama gera o embedding da pergunta.
2. ChromaDB e BM25 recuperam trechos oficiais **em paralelo** com a busca no grafo.
3. O KAG devolve relações cujo sujeito/objeto casam com termos da pergunta
   (arestas `rejeitada` são ignoradas).
4. FastAPI envia trechos + bloco “Relações KAG — orientação” ao modelo.
5. A auditoria de citações mantém a regra: resposta sem citação válida é
   recusada.

## Banco e migrações

- `knowledge_entities` / `knowledge_relations` (Alembic
  `20260917_0003` + proveniência `20260918_0004`).
- `docker compose up --build` aplica as migrações via `db-migrate`.
- Documentos já existentes: reenvie-os para popular o grafo.

## Protocolo `GraphStore`

Interface em `app/services/graph_store.py`:

- `PostgresGraphStore` = `KnowledgeGraphService` (local)
- `NeptuneGraphStore` = stub opcional (`app/services/neptune_graph_store.py`)

Sem chaves AWS no código. Produção futura: implementar openCypher HTTP
contra `NEPTUNE_ENDPOINT` com IAM/SigV4 via role ou variáveis de ambiente.

## Uso em saúde — limites

A aplicação **não** diagnostica, **não** prescreve, **não** substitui o
julgamento clínico e **não** recomenda deixar de solicitar exames. O
disclaimer e o system prompt reforçam isso. Fontes e evidências são
apresentadas para apoio a profissionais; a decisão clínica permanece humana.

## Próximos passos (Neptune / AWS)

1. Provisionar cluster Neptune (openCypher) em VPC.
2. Implementar o cliente HTTP em `neptune_graph_store.py` (sem hardcode de keys).
3. Dual-write opcional (`postgres` + `neptune`) para validar Recall antes do cutover.
4. Trocar `KAG_GRAPH_BACKEND=neptune` só após testes e migração de dados.

Planos relacionados: [KAG-EXTRACAO-LLM.md](KAG-EXTRACAO-LLM.md),
[PGVECTOR-PLANO.md](PGVECTOR-PLANO.md).
