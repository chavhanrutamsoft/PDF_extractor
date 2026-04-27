from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExtractedTable(BaseModel):
    """One accepted table with metadata for JSON / DataFrame export."""

    table_id: str
    page: int = Field(description="1-based PDF page number")
    page_index_0: int = Field(description="0-based page index for developers")
    keywords_matched: list[str] = Field(default_factory=list)
    match_mode: str = ""
    extractor: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str | None]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None
    page_score: int = 0


class ExtractionResult(BaseModel):
    """Full run output."""

    source_pdf: str
    tables: list[ExtractedTable] = Field(default_factory=list)
    skipped_pages: list[int] = Field(
        default_factory=list, description="1-based pages skipped by pre-filter"
    )

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
