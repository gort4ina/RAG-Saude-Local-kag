"""Testes do splitter estrutural.

Lembrete: chunk_chars e overlap_chars sao CARACTERES, nao tokens.
"""

from app.services.text_splitter import split_structured, split_text


def test_empty_text_returns_empty_list() -> None:
    assert split_text("  \n  ") == []


def test_short_text_is_kept_together() -> None:
    assert split_text("Primeiro paragrafo.\nSegundo paragrafo.", 100, 10) == [
        "Primeiro paragrafo.\nSegundo paragrafo."
    ]


def test_long_text_creates_multiple_chunks() -> None:
    text = " ".join(f"frase numero {index}." for index in range(200))
    chunks = split_text(text, chunk_size=300, overlap=40)
    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)


def test_invalid_overlap_raises_value_error() -> None:
    try:
        split_text("texto", chunk_size=100, overlap=100)
    except ValueError:
        return
    raise AssertionError("Era esperado ValueError")


def test_article_number_stays_with_its_content() -> None:
    text = (
        "CAPITULO II\n"
        "DAS BOAS PRATICAS\n\n"
        "Art. 5\n"
        "A farmacia deve manter registro de todas as formulas manipuladas.\n\n"
        "Art. 6\n"
        "O responsavel tecnico responde pela conformidade dos processos.\n"
    )
    chunks = split_structured(text, chunk_chars=120, overlap_chars=20)

    # Nenhum chunk pode ser somente o cabecalho do artigo.
    assert all(chunk.body.strip().rstrip(".") not in {"Art. 5", "Art. 6"} for chunk in chunks)
    joined = [chunk.body for chunk in chunks]
    assert any("Art. 5" in body and "registro" in body for body in joined)
    assert any("Art. 6" in body and "responsavel tecnico" in body for body in joined)


def test_structural_context_is_tracked() -> None:
    text = (
        "# RESOLUCAO RDC 67, DE 8 DE OUTUBRO DE 2007\n\n"
        "CAPITULO I\n"
        "DAS DISPOSICOES INICIAIS\n\n"
        "SECAO II\n"
        "DO PESSOAL\n\n"
        "Art. 12\n"
        "A equipe deve receber treinamento inicial e continuado.\n"
    )
    chunks = split_structured(text, chunk_chars=200, overlap_chars=30)
    last = chunks[-1]
    assert last.context.chapter is not None
    assert "SECAO II" in (last.context.section or "").upper()
    assert last.context.article == "Art. 12"
    assert last.context.item_label() == "Art. 12"


def test_paragraph_and_inciso_are_captured() -> None:
    text = (
        "Art. 20\n"
        "Sao deveres do estabelecimento:\n"
        "I - manter documentacao atualizada;\n"
        "II - permitir o acesso da fiscalizacao.\n"
        "§ 1 O descumprimento sujeita o infrator as penalidades cabiveis.\n"
    )
    chunks = split_structured(text, chunk_chars=1000, overlap_chars=50)
    label = chunks[-1].context.item_label() or ""
    assert "Art. 20" in label


def test_annex_starts_a_new_context() -> None:
    text = (
        "Art. 1\nDisposicoes gerais do documento principal.\n\n"
        "ANEXO I\n"
        "MODELO DE FORMULARIO DE INSPECAO\n"
        "Campo 1: identificacao do estabelecimento.\n"
    )
    chunks = split_structured(text, chunk_chars=120, overlap_chars=20)
    assert any((chunk.context.annex or "").upper().startswith("ANEXO I") for chunk in chunks)


def test_markdown_heading_sets_document_title() -> None:
    text = "# Guia interno de qualidade\n\nConteudo do guia com detalhes suficientes."
    chunks = split_structured(text, chunk_chars=500, overlap_chars=50)
    assert chunks[0].context.title == "Guia interno de qualidade"


def test_oversized_article_is_split_without_losing_text() -> None:
    body = " ".join(f"obrigacao {index} do estabelecimento." for index in range(80))
    text = f"Art. 7\n{body}"
    chunks = split_structured(text, chunk_chars=300, overlap_chars=50)
    assert len(chunks) > 1
    assert all(chunk.context.article == "Art. 7" for chunk in chunks)
