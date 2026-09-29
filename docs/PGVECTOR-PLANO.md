# Plano de migração — ChromaDB → pgvector (paralelo)

## Motivação

- **Uma única fonte de estado transacional.** O KAG já vive no Postgres. Puxar
  os vetores para lá consolida backups, replicação, retenção LGPD, e permite
  cross-join direto (`SELECT ... JOIN knowledge_relations ...`).
- **Menos superfície operacional.** Um serviço a menos para dimensionar,
  monitorar, atualizar e proteger.
- **Migração em serviços gerenciados.** Managed Postgres com `pgvector` (RDS,
  Cloud SQL, Supabase, Neon) é hoje um item de menu; ChromaDB em produção
  gerenciada ainda é auto-hospedagem.

## Regras de invariância

- **Sem quebrar a suíte.** `RagService` já depende apenas de `VectorStorePort`.
- **Sem misturar embeddings.** A tabela precisa carregar `embedding_model` na
  linha ou na coleção (equivalente ao carimbo atual em `chroma.metadata`).
- **Multi-tenant estrito.** Toda query recebe `tenant_id` e o *índice* precisa
  incluí-lo (índice parcial ou índice composto) para o planejador não
  ignorar o filtro.
- **Rollback trivial.** Enquanto ChromaDB continuar recebendo escritas em
  paralelo (fase 2), qualquer bug no PgVector é revertido em minutos.

## Sequência recomendada

### Fase 0 — hoje (feito)
- `VectorStorePort` documentado (`app/services/vector_port.py`).
- `FakeStore` dos testes já satisfaz o contrato.

### Fase 1 — implementação `PgVectorStore` (1–2 sprints)

1. Migration Alembic:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE TABLE knowledge_chunks (
       id                 TEXT PRIMARY KEY,
       tenant_id          TEXT NOT NULL,
       document_id        TEXT NOT NULL,
       embedding          VECTOR(1024) NOT NULL,  -- dim do embeddinggemma
       content            TEXT NOT NULL,
       metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
       embedding_model    TEXT NOT NULL,
       created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
   );
   CREATE INDEX ix_chunks_tenant_doc
       ON knowledge_chunks (tenant_id, document_id);
   CREATE INDEX ix_chunks_embedding
       ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
       WITH (lists = 100)
       WHERE tenant_id = 'default';  -- por tenant, ver seção abaixo
   ```

2. Implementar `PgVectorStore(VectorStorePort)` em
   `app/services/pgvector_store.py`. Cada método é uma consulta SQL curta;
   os testes existentes reaproveitam-se.

3. `dependencies.get_rag_service` escolhe com base em
   `settings.vector_backend ∈ {"chroma", "pgvector"}`.

### Fase 2 — dual-write (1 sprint)

- `DualWriteStore(VectorStorePort)` recebe as escritas e replica para ambos.
- Leitura continua no Chroma; comparação offline (Recall@k, tempo médio).
- Trigger de rollback: se qualquer métrica cair >5%, volta para Chroma.

### Fase 3 — leitura no pgvector, Chroma read-only (1 sprint)

- `PgVectorStore` como leitura principal. Chroma vira **somente leitura**
  para servir "dumps" antigos até a confiança consolidar.

### Fase 4 — remoção do Chroma (1 sprint)

- Drop das dependências, remoção do serviço `chroma`/volumes do compose,
  atualização do README.

## Notas sobre índices por tenant

`pgvector` não indexa naturalmente por tenant. Duas opções:

1. **Índice parcial por tenant** (para poucos tenants grandes).
   Um `CREATE INDEX ... WHERE tenant_id = '...'` por tenant. Escalável até
   dezenas de tenants; acima disso o catálogo do Postgres começa a pesar.

2. **Índice composto `(tenant_id, embedding vector_cosine_ops)`** com
   `SET LOCAL ivfflat.probes = 10` na query. Funciona para muitos tenants
   pequenos.

A escolha depende da distribuição real: instrumentar em Fase 2 antes de
decidir.

## Estimativa de dimensão do vetor

- `embeddinggemma` padrão: 768 dimensões.
- Reservar `VECTOR(1024)` deixa margem para trocar para `bge-m3` ou
  `nomic-embed-text` sem re-migrar; custo por linha ≈ 4 KB.

## Riscos e mitigações

| Risco | Mitigação |
|-------|-----------|
| Reindex enorme se o modelo de embedding mudar | Já existe `EmbeddingModelMismatchError`; a coluna `embedding_model` na linha replica a proteção |
| Custo de `ivfflat.build` em base grande | Rodar em janela de manutenção; pgvector 0.7+ suporta `HNSW` sem rebuild em background |
| Perda de dados na Fase 2 | Escrita idempotente por `id`; Chroma continua sendo backup |
