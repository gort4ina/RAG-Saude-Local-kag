"""Grade de calibracao de chunking e threshold.

Varia ``CHUNK_CHARS``, ``CHUNK_OVERLAP_CHARS`` e ``MIN_RELEVANCE_SCORE``
e devolve a grade pronta para um harness externo (ingestao + Recall@5).

Nao reindexa sozinho: cada celula da grade precisa de uma colecao
separada. Use este modulo para gerar o plano e, com a stack no ar,
rodar ``eval.run_eval`` por celula.

    python -m eval.sweep --output eval/results/sweep-plan.json
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any


DEFAULT_CHUNK_CHARS = (1400, 1900, 2400)
DEFAULT_OVERLAPS = (200, 285, 400)
DEFAULT_SCORES = (0.35, 0.45, 0.55, 0.65)


def build_grid(
    chunk_chars: tuple[int, ...] = DEFAULT_CHUNK_CHARS,
    overlaps: tuple[int, ...] = DEFAULT_OVERLAPS,
    scores: tuple[float, ...] = DEFAULT_SCORES,
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for chars, overlap, score in itertools.product(chunk_chars, overlaps, scores):
        if overlap >= chars:
            continue
        cells.append(
            {
                "chunk_chars": chars,
                "chunk_overlap_chars": overlap,
                "min_relevance_score": score,
                "collection_name": f"sweep_{chars}_{overlap}_{int(score * 100)}",
            }
        )
    return cells


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    grid = build_grid()
    payload = {"cells": grid, "count": len(grid)}
    text = json.dumps(payload, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
