"""Testes de extracao de documentos."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.errors import EmptyDocumentError, ScannedPdfError, UnsupportedDocumentError
from app.services.document_loader import (
    document_hash,
    extract_document,
    extract_pages,
    validate_extension,
)


def test_hash_is_deterministic() -> None:
    assert document_hash(b"conteudo") == document_hash(b"conteudo")
    assert len(document_hash(b"conteudo")) == 24


def test_accepts_supported_extensions() -> None:
    assert validate_extension("GUIA.PDF") == ".pdf"
    assert validate_extension("notas.md") == ".md"


def test_rejects_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "arquivo.exe"
    path.write_bytes(b"binario")
    with pytest.raises(UnsupportedDocumentError):
        extract_document(path)


def test_extracts_utf8_text(tmp_path: Path) -> None:
    path = tmp_path / "fonte.txt"
    path.write_text("Informação educativa com conteúdo suficiente.", encoding="utf-8")
    pages = extract_pages(path)
    assert pages[0].number == 1
    assert "Informação" in pages[0].text


def test_extracts_latin1_text(tmp_path: Path) -> None:
    path = tmp_path / "legado.txt"
    path.write_bytes("Informação regulatória em codificação antiga.".encode("cp1252"))
    pages = extract_pages(path)
    assert "Informação" in pages[0].text


def test_reports_chars_per_page(tmp_path: Path) -> None:
    path = tmp_path / "fonte.txt"
    text = "Conteudo regulatorio com tamanho suficiente para passar no minimo."
    path.write_text(text, encoding="utf-8")
    result = extract_document(path)
    assert result.chars_per_page == {1: len(text)}
    assert result.total_chars == len(text)
    assert result.extraction_ratio == 1.0


def test_short_text_file_is_rejected_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "curto.txt"
    path.write_text("oi", encoding="utf-8")
    with pytest.raises(EmptyDocumentError):
        extract_document(path, min_chars_per_page=40)


def _blank_pdf(path: Path, pages: int = 3) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_pdf_without_text_layer_asks_for_ocr(tmp_path: Path) -> None:
    path = _blank_pdf(tmp_path / "digitalizado.pdf")
    with pytest.raises(ScannedPdfError) as info:
        extract_document(path)
    assert "OCR" in info.value.user_message
    assert info.value.total_pages == 3
    assert info.value.pages_with_text == 0


def test_corrupted_pdf_is_reported_as_empty_document(tmp_path: Path) -> None:
    path = tmp_path / "quebrado.pdf"
    path.write_bytes(b"isto nao e um PDF valido")
    with pytest.raises(EmptyDocumentError):
        extract_document(path)


def test_ocr_backend_is_used_when_provided(tmp_path: Path) -> None:
    path = _blank_pdf(tmp_path / "digitalizado.pdf", pages=1)

    class FakeOcr:
        def extract(self, pdf_path: Path) -> dict[int, str]:
            return {1: "Texto recuperado por OCR com tamanho mais que suficiente."}

    result = extract_document(path, ocr=FakeOcr())
    assert result.pages_with_text == 1
    assert "OCR" in result.pages[0].text
