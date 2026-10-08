"""OCR opcional por tras do ``OcrBackend``.

Nao e dependencia obrigatoria. Se ``tesseract`` nao estiver no PATH, o
backend declara-se indisponivel e o loader continua rejeitando PDF
digitalizado com ``ScannedPdfError``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class TesseractOcrBackend:
    """Chama o binario ``tesseract`` pagina a pagina via ``pdftoppm``."""

    def available(self) -> bool:
        return shutil.which("tesseract") is not None and shutil.which("pdftoppm") is not None

    def extract(self, path: Path) -> dict[int, str]:
        if not self.available():
            logger.info("ocr_backend_unavailable")
            return {}

        work = path.parent / f".ocr-{path.stem}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["pdftoppm", "-png", str(path), str(work / "page")],
                check=True,
                capture_output=True,
                timeout=120,
            )
            pages: dict[int, str] = {}
            images = sorted(work.glob("page*.png"))
            for index, image in enumerate(images, start=1):
                completed = subprocess.run(
                    ["tesseract", str(image), "stdout", "-l", "por+eng"],
                    check=False,
                    capture_output=True,
                    timeout=60,
                    text=True,
                )
                text = (completed.stdout or "").strip()
                if text:
                    pages[index] = text
            return pages
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("ocr_failed", extra={"error": type(exc).__name__})
            return {}
        finally:
            for child in work.glob("*"):
                child.unlink(missing_ok=True)
            work.rmdir()
