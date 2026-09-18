from enum import StrEnum

from pydantic import BaseModel, Field


class TextSource(StrEnum):
    TEXT_LAYER = "TEXT_LAYER"
    OCR = "OCR"


class BoundingBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class OcrBlock(BaseModel):
    page_number: int = Field(ge=1)
    text: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    bounding_box: BoundingBox
    source: TextSource


class DocumentProcessingResult(BaseModel):
    document_id: str
    blocks: list[OcrBlock]
