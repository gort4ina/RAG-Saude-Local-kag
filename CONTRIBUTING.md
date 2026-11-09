# Como contribuir

Este documento define como organizamos **branches**, **commits** e **versões**. O objetivo é que o histórico conte a história do projeto e que `main` esteja sempre pronta para subir.

## 1. Branches

```
main ────●─────────────●──────────●───  só recebe release/hotfix (tag vX.Y.Z)
          \           /          /
develop ───●──●──●───●──●──●────●────   integração; todo PR de trabalho entra aqui
              \  \  /      \  /
               \  ●─●        ●          branches de trabalho (curtas)
```

| Branch | Para quê | Quem escreve | Regras |
|--------|----------|--------------|--------|
| `main` | Código estável, o que está (ou vai estar) em produção. Cada release recebe uma tag `vX.Y.Z`. | Só via PR vindo de `develop` ou `hotfix/*` | Protegida: PR obrigatório, CI verde, sem *force push* |
| `develop` | Integração do que está pronto e testado. | Só via PR de branches de trabalho | Protegida: PR obrigatório, CI verde |
| `feat/*` `fix/*` `perf/*` `refactor/*` `test/*` `docs/*` `chore/*` `ci/*` | Uma mudança com um propósito só. | Você | Nascem de `develop`, voltam para `develop` por PR, são apagadas após o merge |
| `hotfix/*` | Correção urgente em produção. | Você | Nasce de `main`, volta para `main` **e** `develop` |
| `archive/*` | Tags (não branches) que preservam trabalho antigo antes de apagar a branch. | — | Somente leitura |

### Nome das branches

`<tipo>/<assunto-curto-em-kebab-case>`, em português, sem acento:

```
feat/fila-de-ingestao
fix/vazamento-do-gate
perf/cache-de-resposta
docs/guia-de-deploy
chore/atualiza-dependencias
```

Use o **mesmo tipo do commit principal**. Uma branch = um assunto. Se o PR precisa de "e também", são duas branches.

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
4. Não reescreva histórico de branch já publicada (`push --force`), exceto na sua branch de trabalho antes do merge, e só com `--force-with-lease`.
5. Merge de PR: **squash** para branches de trabalho com commits de rascunho; **merge commit** (`--no-ff`) para `develop → main`, mantendo o histórico da release.

## 3. Fluxo de trabalho

```bash
git switch develop && git pull
git switch -c fix/assunto-curto

# ... trabalhe, rode os testes ...
python -m pytest -q

git add <arquivos>             # evite `git add .`
git commit                     # mensagem no padrão acima
git push -u origin fix/assunto-curto
# abra o PR para develop
```

Antes de abrir o PR: testes passando, sem arquivos de ambiente, mensagem de commit no padrão, PR descrevendo o **porquê** (o template ajuda).

## 4. Versões e releases

[Versionamento semântico](https://semver.org/lang/pt-BR/): `MAJOR.MINOR.PATCH`.

- Enquanto o projeto for protótipo, usamos `0.y.z`: mudanças incompatíveis sobem o `y`.
- Release: PR `develop → main`, depois a tag anotada na `main`:

```bash
git switch main && git pull
git tag -a v0.1.0 -m "v0.1.0: base inicial com autenticação, RAG híbrido e KAG"
git push origin v0.1.0
```

## 5. Segredos

- `.env` e variações **nunca** vão para o Git. O `.gitignore` bloqueia tudo que começa com `.env`, exceto `.env.example` e `.env.production.example`.
- Se um segredo vazar: **troque o segredo primeiro** (apagar do histórico não adianta, ele já foi copiado), depois limpe o histórico se necessário.
- A API recusa subir em produção com valores de exemplo (`troque-...`).
