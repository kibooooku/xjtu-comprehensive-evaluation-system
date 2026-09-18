from pathlib import Path
from typing import Protocol

from app.domain.models import DocumentProcessingResult


class DocumentProcessor(Protocol):
    """Returns text blocks and coordinates without making business decisions."""

    def process(self, document_id: str, pdf_path: Path) -> DocumentProcessingResult:
        ...
