# Plano — Extração LLM-assistida para o KAG

O extractor por dicionário (`DictionaryRelationExtractor`) é barato,
determinístico e cobre bem os relacionamentos de cabeçalho e os tópicos
regulatórios frequentes. Ele não cobre o que dá valor real ao grafo:
relações *entre normas* ("RDC 67/2007 revoga RDC 33/2000"), *entre atores*
("SUSFARM regula ANVISA"), *entre requisitos* ("Art. 5 exige POP").

O ``RelationExtractor`` protocol (`app/services/knowledge_graph.py`) já
aceita implementações plugáveis. O plano abaixo especifica **como**
inserir extração LLM sem perder auditabilidade.

## Invariantes

- **Toda aresta LLM entra como `confidence < 1.0` e
  `validation_status = "pendente_de_validacao"`**. A UI já mostra o badge
  "extração LLM (confiança X%)".
- **Toda aresta LLM carrega `source_span`** apontando para o chunk que a
  gerou. Um humano precisa poder pular direto para o trecho.
- **Sem validação humana, uma aresta LLM nunca é apresentada como fato:**
  o RAG a injeta no prompt sob a rubrica "Relações KAG — orientação;
  confirme sempre pelas Fontes".
- **O modelo de extração é local** (mesmo Ollama, `qwen2.5:7b` ou
  `qwen2.5:14b`). Nenhum dado sai do cluster.

## Arquitetura

```
Upload → ingest → chunks estruturais → fila `kag.extract`
                                       │
                                       ▼
                                 LLMExtractor worker
                                       │
                                       ▼
                     KnowledgeRelation (extracted_by=llm,
                                        confidence=x,
                                        validation_status=pendente)
                                       │
                                       ▼
                          Painel Angular /admin/kag
                          (aprovar / rejeitar / editar)
```

### Fila e worker

- Reaproveita `InferenceGate` para não pisar na inferência do chat.
- Fila leve: uma tabela `kag_extraction_jobs` no Postgres com
  `(tenant_id, document_id, status, attempts)`. Roda em background no
  `lifespan` (asyncio task) ou em worker separado se o volume crescer.
- Retry com backoff exponencial; job "morto" após 5 tentativas fica em
  `dead-letter` para inspeção.

### Prompt

O prompt precisa forçar saída estruturada. Exemplo de esqueleto:

```
Você é um extractor de relações regulatórias. Dado o trecho abaixo,
extraia até 3 arestas no formato JSONL:
  {"subject": "...", "relation": "...", "object": "...",
   "confidence": 0..1, "evidence": "citação literal do trecho"}

Vocabulário permitido de relações:
  - "revoga", "revogada_por"
  - "altera", "alterada_por"
  - "regula" (assunto)
  - "exige"
  - "emitida_por"
  - "aplica_a"

Não invente. Se nenhuma relação for extraível, retorne [].
```

O parser rejeita:
- JSON inválido → descarta a aresta;
- `subject`/`object` que não são substrings do trecho → descarta (guarda
  contra alucinação);
- `confidence` fora de `[0, 1]` → clip.

### Rate limit natural

`kag.extract` compete com `rag.query` pelo `InferenceGate`. Isso significa
que em uso intenso, a extração é interrompida — o **usuário** tem
prioridade. Aceitável: extração de KAG pode ser batch.

## Métricas (Prometheus)

Adicionar em `app/services/metrics.py`:

```python
kag_extraction_relations = Counter(
    "rag_kag_llm_relations_total",
    "Relações KAG extraídas pelo LLM.",
    labelnames=("outcome",),  # kept | rejected | invalid_json
)
kag_extraction_duration = Histogram(
    "rag_kag_llm_extraction_duration_seconds",
    "Tempo por chunk na extração LLM.",
    buckets=(1, 5, 15, 30, 60, 120, 300),
)
```

## Roadmap sugerido

1. **Sprint 1 — fila e worker sem LLM.** Estrutura de job, retry, dashboard.
2. **Sprint 2 — LLMExtractor com prompt V1.** Modelo pequeno (qwen2.5:3b)
   para calibração, saída sempre `pendente_de_validacao`.
3. **Sprint 3 — Painel Angular `/admin/kag`.** Aprovar/rejeitar em lote,
   filtro por `extracted_by=llm`, atalho para o trecho de origem.
4. **Sprint 4 — Modelo maior + prompt V2 com few-shot regulatório.**
   Comparar precision/recall contra o dicionário; publicar dashboard.

## Como isso NÃO quebra citação

A resposta continua sendo auditada por `citations.audit_answer` contra as
Fontes originais (chunks textuais do documento). O KAG entra como
*contexto adicional* no prompt, mas o modelo é instruído a citar
`[Fonte N]`, não `[Relação N]`. Isso é intencional: mesmo uma aresta
validada por humano se refere a uma norma; a citação continua sendo a
norma.
