from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class PdfPlumberTableSettings(BaseModel):
    """Subset of pdfplumber `table_settings` (see pdfplumber docs)."""

    vertical_strategy: Literal["lines", "lines_strict", "text", "explicit"] = "lines"
    horizontal_strategy: Literal["lines", "lines_strict", "text", "explicit"] = "lines"
    snap_tolerance: float = 3
    snap_x_tolerance: float | None = None
    snap_y_tolerance: float | None = None
    join_tolerance: float = 3
    join_x_tolerance: float | None = None
    join_y_tolerance: float | None = None
    edge_min_length: float = 3
    min_words_vertical: int = 3
    min_words_horizontal: int = 1
    intersection_tolerance: float = 3
    text_tolerance: float = 3
    text_x_tolerance: float | None = None
    text_y_tolerance: float | None = None

    def to_pdfplumber_dict(self) -> dict:
        d = self.model_dump()
        out = {}
        for k, v in d.items():
            if v is None:
                continue
            out[k] = v
        return out


class ExtractionConfig(BaseModel):
    """Keyword rules, thresholds, and extractor toggles."""

    keywords: list[str] = Field(
        default_factory=lambda: [
            "Rated Current",
            "Breaking Capacity",
            "MRP",
            "EasyPact CVS",
        ]
    )
    regex_patterns: list[str] = Field(
        default_factory=list,
        description="Optional regexes (e.g. product family) evaluated on normalized text",
    )
    match_mode: Literal["header_any", "header_all", "any", "all"] = "header_any"
    header_row_count: int = Field(default=3, ge=1, le=10)
    min_keyword_hits: int = Field(default=1, ge=1)
    page_score_min: int = Field(
        default=0,
        ge=0,
        description="Skip table extraction on pages with page_score below this (0 = never skip)",
    )
    use_page_prefilter: bool = True
    forward_fill_column_indices: list[int] = Field(
        default_factory=lambda: [0],
        description="Forward-fill empty/None cells in these column indices within each row (e.g. category)",
    )
    never_forward_fill_numeric_columns: bool = True
    header_synonyms: dict[str, str] = Field(
        default_factory=dict,
        description="Map normalized header substring to canonical slug (optional)",
    )
    price_column_regex: str = r"(?i)\b(mrp|list\s*price|price|inr|rs\.?)\b"
    table_settings: PdfPlumberTableSettings = Field(default_factory=PdfPlumberTableSettings)
    use_camelot_fallback: bool = False
    camelot_pages: str = "all"
    camelot_row_tol: int = 10
    camelot_edge_tol: int = 50
    run_word_qa: bool = True
    multiline_join: str = " "
    drop_tables_with_no_data_rows: bool = Field(
        default=True,
        description="Skip tables that only contain header lines (no body rows after split)",
    )

    @staticmethod
    def from_yaml(path: str | Path) -> ExtractionConfig:
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ExtractionConfig.model_validate(data)
