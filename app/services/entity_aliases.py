"""Aliases canonicos de entidades regulatorias frequentes.

Nao tenta cobrir o dominio inteiro. So o que o detector de autoridade
ja reconhece — para o grafo achar "ANVISA" quando a pergunta diz
"Agencia Nacional de Vigilancia Sanitaria".
"""

from __future__ import annotations

from app.services.normalize import normalize_entity_name

# chave = nome normalizado da entidade canonica
DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "anvisa": (
        "agencia nacional de vigilancia sanitaria",
        "agência nacional de vigilância sanitária",
        "anvisa",
    ),
    "ministerio da saude": (
        "ministério da saúde",
        "ministerio da saude",
        "ms",
    ),
    "cff": (
        "conselho federal de farmacia",
        "conselho federal de farmácia",
    ),
    "vigilancia sanitaria": (
        "vigilância sanitária",
        "visa",
    ),
}


def aliases_for(name: str) -> tuple[str, ...]:
    key = normalize_entity_name(name)
    return DEFAULT_ALIASES.get(key, ())
