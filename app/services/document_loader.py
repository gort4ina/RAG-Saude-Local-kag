"""Extracao segura de texto de PDF, TXT e Markdown.

Este modulo NAO executa OCR. Quando um PDF nao tem camada de texto, a
extracao falha de forma explicita com ``ScannedPdfError`` para que o
usuario saiba que precisa aplicar OCR antes de enviar. O ponto de extensao
``OcrBackend`` esta preparado para receber um OCR real no futuro sem mudar
o restante do pipeline.

Todo o trabalho aqui e sincrono e pesado (pypdf). O chamador deve executa-lo
em um thread pool, nunca no event loop.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.errors import EmptyDocumentError, ScannedPdfError, UnsupportedDocumentError

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md"}


@dataclass(frozen=True, slots=True)
class DocumentPage:
    """Conteudo textual de uma pagina ou unidade logica."""

    number: int
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Paginas extraidas mais o diagnostico da extracao."""

    pages: list[DocumentPage]
    total_pages: int
    chars_per_page: dict[int, int]

    @property
    def pages_with_text(self) -> int:
        return len(self.pages)

    @property
    def total_chars(self) -> int:
        return sum(self.chars_per_page.values())

    @property
    def extraction_ratio(self) -> float:
        if self.total_pages == 0:
            return 0.0
        return self.pages_with_text / self.total_pages


@runtime_checkable
class OcrBackend(Protocol):
    """Ponto de extensao para um OCR futuro.

    Uma implementacao deve receber o caminho do PDF e devolver o texto por
    numero de pagina. Nenhuma implementacao e fornecida hoje: OCR e pesado
    e disputaria CPU com o restante do pipeline.
    """

    def extract(self, path: Path) -> dict[int, str]:  # pragma: no cover - contrato
        ...


def validate_extension(filename: str) -> str:
    """Retorna a extensao permitida ou levanta erro."""
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Formato nao suportado. Use PDF, TXT ou Markdown.")
    return extension


def validate_content_signature(extension: str, content: bytes) -> None:
    """Rejeita conteúdo obviamente incompatível com a extensão declarada."""
    if extension == ".pdf" and not content.lstrip().startswith(b"%PDF-"):
        raise ValueError("assinatura PDF ausente")
    if extension in {".txt", ".md"} and b"\x00" in content[:8192]:
        raise ValueError("conteúdo binário enviado como texto")


def document_hash(content: bytes) -> str:
    """Identificador curto e estavel usado para evitar duplicidade."""
    return hashlib.sha256(content).hexdigest()[:24]


def _read_pdf(path: Path, min_chars_per_page: int) -> ExtractionResult:
    try:
        reader = PdfReader(str(path))
        raw_pages = list(reader.pages)
    except (PdfReadError, OSError, ValueError) as exc:
        logger.warning("pdf_unreadable", extra={"error": type(exc).__name__})
        raise EmptyDocumentError(
            "Nao foi possivel ler o PDF enviado. O arquivo pode estar corrompido "
            "ou protegido por senha."
        ) from exc

    chars_per_page: dict[int, int] = {}
    pages: list[DocumentPage] = []
    for index, page in enumerate(raw_pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # pragma: no cover - pypdf falha em paginas exoticas
            logger.warning("pdf_page_extract_failed", extra={"page": index})
            text = ""
        chars_per_page[index] = len(text)
        if len(text) >= min_chars_per_page:
            pages.append(DocumentPage(number=index, text=text))

    return ExtractionResult(
        pages=pages, total_pages=len(raw_pages), chars_per_page=chars_per_page
    )


def _read_plain_text(path: Path, min_chars_per_page: int) -> ExtractionResult:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding).strip()
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 nunca falha
        text = raw.decode("utf-8", errors="replace").strip()

    pages = [DocumentPage(number=1, text=text)] if len(text) >= min_chars_per_page else []
    return ExtractionResult(pages=pages, total_pages=1, chars_per_page={1: len(text)})


def extract_document(
    path: Path,
    *,
    min_chars_per_page: int = 40,
    min_extraction_ratio: float = 0.20,
    ocr: OcrBackend | None = None,
) -> ExtractionResult:
    """Extrai o texto de um arquivo ja validado.

    Levanta ``ScannedPdfError`` quando a proporcao de paginas com texto fica
    abaixo de ``min_extraction_ratio``, sinalizando necessidade de OCR.
    """
    try:
        extension = validate_extension(path.name)
    except ValueError as exc:
        raise UnsupportedDocumentError() from exc

    if extension == ".pdf":
        result = _read_pdf(path, min_chars_per_page)
        if not result.pages and ocr is not None:  # pragma: no cover - sem OCR hoje
            recovered = ocr.extract(path)
            pages = [
                DocumentPage(number=number, text=text.strip())
                for number, text in sorted(recovered.items())
                if len(text.strip()) >= min_chars_per_page
            ]
            result = ExtractionResult(
                pages=pages,
                total_pages=result.total_pages,
                chars_per_page={
                    number: len(text) for number, text in recovered.items()
                },
            )
    else:
        result = _read_plain_text(path, min_chars_per_page)

    logger.info(
        "document_extracted",
        extra={
            "total_pages": result.total_pages,
            "pages_with_text": result.pages_with_text,
            "total_chars": result.total_chars,
            "extraction_ratio": round(result.extraction_ratio, 3),
        },
    )

    if not result.pages:
        if extension == ".pdf":
            raise ScannedPdfError(0, result.total_pages)
        raise EmptyDocumentError()

    if extension == ".pdf" and result.extraction_ratio < min_extraction_ratio:
        raise ScannedPdfError(result.pages_with_text, result.total_pages)

    return result


def extract_pages(path: Path) -> list[DocumentPage]:
    """Compatibilidade: apenas as paginas com texto."""
    return extract_document(path).pages
