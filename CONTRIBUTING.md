# Como contribuir

Este documento define como organizamos **branches**, **commits** e **versões**. O objetivo é que o histórico conte a história do projeto e que `main` esteja sempre pronta para subir.

## 1. Branches

Por enquanto o projeto usa **uma branch só: `main`**, sempre com o código atualizado. Não há `develop` nem branches de longa duração.

| Branch | Para quê | Regras |
|--------|----------|--------|
| `main` | Todo o código atualizado. Cada release recebe uma tag `vX.Y.Z`. | Os testes precisam passar antes de cada `push`. Sem *force push*. |
| `<tipo>/<assunto>` (opcional) | Uma mudança grande ou arriscada que você quer revisar em PR antes de entrar. | Nasce de `main`, volta para `main`, é apagada após o merge. |

Para mudanças pequenas, commite direto na `main`. Quando o time crescer, volta-se ao modelo `main` + `develop` (com branches `feat/*`, `fix/*`, `hotfix/*`) e à proteção de branches no GitHub.

### Nome das branches temporárias

`<tipo>/<assunto-curto-em-kebab-case>`, em português, sem acento, com o **mesmo tipo do commit principal**:

```
feat/fila-de-ingestao
fix/vazamento-do-gate
perf/cache-de-resposta
```

Uma branch = um assunto. Se o PR precisa de "e também", são duas branches.

## 2. Commits

Seguimos o [Conventional Commits](https://www.conventionalcommits.org/pt-br/v1.0.0/), em português:

```
<tipo>(<escopo>): <resumo no imperativo, até ~70 caracteres, sem ponto final>

<corpo opcional: o PORQUÊ, não o quê. Quebre em ~72 colunas.>

<rodapé opcional: Auditoria: P1 · Refs: #12 · BREAKING CHANGE: ...>
```

### Tipos

| Tipo | Quando usar | Efeito na versão |
|------|-------------|------------------|
| `feat` | Funcionalidade nova para quem usa o sistema | minor |
| `fix` | Correção de defeito | patch |
| `perf` | Melhora de desempenho sem mudar comportamento | patch |
| `refactor` | Reorganiza código sem mudar comportamento | — |
| `test` | Só testes | — |
| `docs` | Só documentação | — |
| `ci` | Pipeline, GitHub Actions | — |
| `build` | Dockerfile, compose, dependências | — |
| `chore` | Manutenção que não cabe nos outros | — |
| `revert` | Desfaz um commit anterior | depende |

`BREAKING CHANGE:` no rodapé (ou `!` após o escopo, ex.: `feat(api)!:`) sobe a versão **major**.

### Escopos

Use um destes (ou omita se a mudança atravessa tudo):

`api` · `auth` · `rag` · `retrieval` · `llm` · `ollama` · `vector-store` · `kag` · `ingestao` · `observabilidade` · `config` · `frontend` · `docker` · `ci` · `docs` · `seguranca`

### Exemplos bons

```
perf(vector-store): cacheia count_chunks por tenant
fix(streaming): garante a devolução da vaga do InferenceGate
feat(config): perfil de produção e recusa de valores de exemplo
docs(arquitetura): auditoria de segurança, performance, escala e LLM
```

### Regras

1. **Um commit = uma mudança que passa nos testes sozinha.** Não misture refatoração com correção.
2. O resumo diz **o que muda**; o corpo diz **por que**. O "como" está no diff.
3. Não commite segredos, nem "só para testar". Veja a seção 5.
4. Não reescreva histórico já publicado (`push --force`). Em branch temporária ainda não mesclada, só com `--force-with-lease`.
5. Evite `git add .`: adicione os arquivos da mudança, para que cada commit tenha um assunto só.

## 3. Fluxo de trabalho

```bash
git switch main && git pull
python -m pytest -q            # parte de uma base verde

# ... trabalhe ...

python -m pytest -q
git add <arquivos>
git commit                     # mensagem no padrão acima
git push
```

## 4. Versões e releases

[Versionamento semântico](https://semver.org/lang/pt-BR/): `MAJOR.MINOR.PATCH`.

- Enquanto o projeto for protótipo, usamos `0.y.z`: mudanças incompatíveis sobem o `y`.
- Release: tag anotada na `main`.

```bash
git switch main && git pull
git tag -a v0.1.0 -m "v0.1.0: base inicial com autenticação, RAG híbrido e KAG"
git push origin v0.1.0
```

## 5. Segredos

- `.env` e variações **nunca** vão para o Git. O `.gitignore` bloqueia tudo que começa com `.env`, exceto `.env.example` e `.env.production.example`.
- Se um segredo vazar: **troque o segredo primeiro** (apagar do histórico não adianta, ele já foi copiado), depois limpe o histórico se necessário.
- A API recusa subir em produção com valores de exemplo (`troque-...`).
