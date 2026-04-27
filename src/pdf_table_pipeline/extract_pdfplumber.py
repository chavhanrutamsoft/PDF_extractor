from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pdfplumber

from pdf_table_pipeline.config import ExtractionConfig


@dataclass
class PdfPlumberTableCandidate:
    """Raw table from pdfplumber before filtering."""

    page_1based: int
    page_index_0: int
    table_index: int
    bbox: tuple[float, float, float, float]
    rows: list[list[str | None]]
    extractor: str = "pdfplumber"


def extract_tables_pdfplumber_page(
    page: pdfplumber.page.Page,
    page_1based: int,
    config: ExtractionConfig,
) -> list[PdfPlumberTableCandidate]:
    """Find and extract all tables on a single page."""
    settings = config.table_settings.to_pdfplumber_dict()
    found = page.find_tables(table_settings=settings)
    out: list[PdfPlumberTableCandidate] = []
    for idx, table in enumerate(found):
        try:
            data = table.extract()
        except Exception:
            data = None
        if not data:
            continue
        bbox = tuple(float(x) for x in table.bbox)
        out.append(
            PdfPlumberTableCandidate(
                page_1based=page_1based,
                page_index_0=page_1based - 1,
                table_index=idx,
                bbox=bbox,
                rows=_ensure_rectangular(data),
            )
        )
    return out


def _ensure_rectangular(data: list[list[Any | None]]) -> list[list[str | None]]:
    if not data:
        return []
    max_cols = max(len(r) for r in data)
    rect: list[list[str | None]] = []
    for row in data:
        cells: list[str | None] = []
        for i in range(max_cols):
            if i < len(row):
                c = row[i]
                if c is None:
                    cells.append(None)
                else:
                    cells.append(str(c).strip() if str(c).strip() else None)
            else:
                cells.append(None)
        rect.append(cells)
    return rect


def open_pdf(path: str) -> pdfplumber.PDF:
    return pdfplumber.open(path)
