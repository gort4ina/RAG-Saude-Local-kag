"""Avaliador da RAG regulatoria.

Executa cada pergunta de ``eval/dataset.json`` contra uma instancia REAL da
aplicacao (API HTTP) e calcula as metricas de qualidade e latencia.

O avaliador nao inventa numeros: se ele nao rodar, nao ha resultado. Todos os
valores impressos vem das respostas efetivamente recebidas.

Uso tipico (com os containers no ar):

    python -m eval.run_eval --base-url http://localhost:8000
    python -m eval.run_eval --limit 5 --output eval/results.json

Metricas calculadas:

- ``recall_at_5``          proporcao de casos em que o documento esperado
                           aparece entre as 5 primeiras fontes recuperadas.
                           Requer ``expected_document`` preenchido.
- ``citation_precision``   proporcao de citacoes [Fonte N] validas.
- ``grounded_rate``        proporcao de respostas marcadas como fundamentadas
                           entre as que deveriam ser respondidas.
- ``abstention_accuracy``  proporcao de perguntas que deveriam ser recusadas e
                           foram de fato recusadas.
- ``keyword_coverage``     proporcao media de palavras-chave esperadas
                           presentes na resposta.
- latencia p50 / p95, tempo de embedding, tempo de carga do modelo e tempo de
  geracao, todos vindos dos campos de metrica da propria resposta.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

DATASET_PATH = Path(__file__).with_name("dataset.json")

# Frase exata que a aplicacao usa quando nao ha evidencia suficiente.
ABSTENTION_MARKERS = (
    "nao encontrei informacao suficiente",
    "ainda nao ha documentos indexados",
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _is_abstention(answer: str) -> bool:
    normalized = _normalize(answer)
    return any(marker in normalized for marker in ABSTENTION_MARKERS)


@dataclass
class CaseResult:
    """Resultado bruto de um caso, sem interpretacao."""

    id: str
    question: str
    should_answer: bool
    http_status: int = 0
    error_code: str | None = None
    answer: str = ""
    grounded: bool = False
    abstained: bool = False
    requires_human_review: bool = False
    cited_sources: int = 0
    retrieved_documents: list[str] = field(default_factory=list)
    expected_document: str | None = None
    document_hit_at_5: bool | None = None
    keyword_coverage: float | None = None
    duration_ms: int = 0
    embed_ms: int = 0
    retrieval_ms: int = 0
    chat_ms: int = 0
    load_duration_ms: int = 0
    tokens_per_second: float = 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * percentile
    lower = ordered[int(rank)]
    upper = ordered[min(int(rank) + 1, len(ordered) - 1)]
    return round(lower + (upper - lower) * (rank - int(rank)), 2)


def evaluate_case(client: httpx.Client, case: dict[str, Any]) -> CaseResult:
    """Roda uma pergunta contra a API e coleta as metricas devolvidas."""
    result = CaseResult(
        id=str(case.get("id", "")),
        question=str(case["question"]),
        should_answer=bool(case.get("should_answer", True)),
        expected_document=case.get("expected_document") or None,
    )

    started = time.perf_counter()
    try:
        response = client.post("/api/chat", json={"question": result.question})
    except httpx.HTTPError as exc:
        result.error_code = f"transport:{type(exc).__name__}"
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    result.http_status = response.status_code
    payload = response.json() if response.content else {}

    if response.status_code != 200:
        result.error_code = str(payload.get("code", f"http_{response.status_code}"))
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    result.answer = str(payload.get("answer", ""))
    result.grounded = bool(payload.get("grounded"))
    result.abstained = _is_abstention(result.answer)
    result.requires_human_review = bool(payload.get("requires_human_review"))
    result.cited_sources = len(payload.get("sources") or [])
    result.duration_ms = int(payload.get("duration_ms") or 0) or int(
        (time.perf_counter() - started) * 1000
    )
    result.embed_ms = int(payload.get("embed_ms") or 0)
    result.retrieval_ms = int(payload.get("retrieval_ms") or 0)
    result.chat_ms = int(payload.get("chat_ms") or 0)
    result.load_duration_ms = int(payload.get("load_duration_ms") or 0)
    result.tokens_per_second = float(payload.get("tokens_per_second") or 0.0)

    retrieved = payload.get("retrieved_sources") or payload.get("sources") or []
    result.retrieved_documents = [
        str(item.get("document", "")) for item in retrieved
    ]

    if result.expected_document:
        expected = _normalize(result.expected_document)
        result.document_hit_at_5 = any(
            expected in _normalize(document)
            for document in result.retrieved_documents[:5]
        )

    keywords = [str(word) for word in (case.get("expected_keywords") or [])]
    if keywords and result.should_answer:
        normalized_answer = _normalize(result.answer)
        hits = sum(1 for word in keywords if _normalize(word) in normalized_answer)
        result.keyword_coverage = round(hits / len(keywords), 3)

    return result


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    """Agrega as metricas. Campos sem dados suficientes viram ``None``."""
    answerable = [item for item in results if item.should_answer]
    refusable = [item for item in results if not item.should_answer]
    successful = [item for item in results if item.http_status == 200]

    with_gold = [item for item in answerable if item.document_hit_at_5 is not None]
    recall_at_5 = (
        round(sum(1 for item in with_gold if item.document_hit_at_5) / len(with_gold), 3)
        if with_gold
        else None
    )

    grounded_answerable = [
        item for item in answerable if item.http_status == 200
    ]
    grounded_rate = (
        round(
            sum(1 for item in grounded_answerable if item.grounded)
            / len(grounded_answerable),
            3,
        )
        if grounded_answerable
        else None
    )

    # Citacao correta = resposta fundamentada com pelo menos uma fonte citada.
    cited = [item for item in grounded_answerable if item.grounded]
    citation_precision = (
        round(sum(1 for item in cited if item.cited_sources > 0) / len(cited), 3)
        if cited
        else None
    )

    refused = [
        item
        for item in refusable
        if item.http_status == 200 and (item.abstained or not item.grounded)
    ]
    abstention_accuracy = (
        round(len(refused) / len(refusable), 3) if refusable else None
    )

    coverages = [
        item.keyword_coverage
        for item in answerable
        if item.keyword_coverage is not None
    ]
    keyword_coverage = (
        round(statistics.mean(coverages), 3) if coverages else None
    )

    latencies = [float(item.duration_ms) for item in successful]
    embed_times = [float(item.embed_ms) for item in successful if item.embed_ms]
    load_times = [
        float(item.load_duration_ms) for item in successful if item.load_duration_ms
    ]
    chat_times = [float(item.chat_ms) for item in successful if item.chat_ms]
    throughput = [
        item.tokens_per_second for item in successful if item.tokens_per_second
    ]

    return {
        "cases_total": len(results),
        "cases_ok": len(successful),
        "cases_failed": len(results) - len(successful),
        "quality": {
            "recall_at_5": recall_at_5,
            "recall_at_5_evaluated_cases": len(with_gold),
            "citation_precision": citation_precision,
            "grounded_rate": grounded_rate,
            "abstention_accuracy": abstention_accuracy,
            "keyword_coverage": keyword_coverage,
        },
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "mean": round(statistics.mean(latencies), 2) if latencies else None,
            "embedding_mean": round(statistics.mean(embed_times), 2) if embed_times else None,
            "model_load_mean": round(statistics.mean(load_times), 2) if load_times else None,
            "generation_mean": round(statistics.mean(chat_times), 2) if chat_times else None,
        },
        "tokens_per_second_mean": (
            round(statistics.mean(throughput), 2) if throughput else None
        ),
        "notes": [
            "recall_at_5 e None quando nenhum caso tem 'expected_document' preenchido.",
            "abstention_accuracy conta como acerto tanto a frase de recusa quanto grounded=false.",
            "Todas as latencias vem dos campos devolvidos pela propria API.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Avaliador da RAG regulatoria.")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="URL da API (padrao: http://localhost:8000)",
    )
    parser.add_argument(
        "--dataset", type=Path, default=DATASET_PATH, help="Arquivo de casos."
    )
    parser.add_argument("--limit", type=int, default=0, help="Roda apenas N casos.")
    parser.add_argument(
        "--timeout", type=float, default=600.0, help="Timeout por pergunta (s)."
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="Grava o relatorio JSON."
    )
    args = parser.parse_args(argv)

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    cases = dataset.get("cases") or []
    if args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        print("Nenhum caso no dataset.", file=sys.stderr)
        return 1

    print(f"Avaliando {len(cases)} caso(s) contra {args.base_url}\n")

    results: list[CaseResult] = []
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        try:
            health = client.get("/api/health")
            health.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"API indisponivel em {args.base_url}: {exc}", file=sys.stderr)
            return 2

        for index, case in enumerate(cases, start=1):
            result = evaluate_case(client, case)
            results.append(result)
            marker = "ok " if result.http_status == 200 else "ERR"
            print(
                f"[{index:>3}/{len(cases)}] {marker} {result.id:<12} "
                f"grounded={str(result.grounded):<5} "
                f"fontes={result.cited_sources} "
                f"{result.duration_ms} ms"
                + (f"  ({result.error_code})" if result.error_code else "")
            )

    summary = summarize(results)
    report = {
        "base_url": args.base_url,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": summary,
        "cases": [asdict(item) for item in results],
    }

    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nRelatorio salvo em {args.output}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
