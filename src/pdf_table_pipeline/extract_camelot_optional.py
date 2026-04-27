from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pdf_table_pipeline.config import ExtractionConfig

logger = logging.getLogger(__name__)


def camelot_available() -> bool:
    try:
        import camelot  # noqa: F401
    except ImportError:
        return False
    return True


def extract_tables_camelot_stream_page(
    pdf_path: str | Path,
    page_1based: int,
    config: ExtractionConfig,
) -> list[list[list[str | None]]]:
    """
    Optional Camelot 'stream' extraction for one page.

    Returns list of tables (each table is rows of cells).
    Empty list if camelot not installed or on error.
    """
    if not config.use_camelot_fallback:
        return []
    if not camelot_available():
        logger.warning("Camelot fallback requested but camelot-py is not installed")
        return []
    import camelot

    pages = str(page_1based)
    try:
        tables = camelot.read_pdf(
            str(pdf_path),
            pages=pages,
            flavor="stream",
            row_tol=config.camelot_row_tol,
            edge_tol=config.camelot_edge_tol,
        )
    except Exception as exc:
        logger.warning("Camelot read_pdf failed for page %s: %s", page_1based, exc)
        return []

    out: list[list[list[str | None]]] = []
    for t in tables:
        try:
            df = t.df
        except Exception:
            continue
        rows: list[list[str | None]] = []
        for _, row in df.iterrows():
            cells = [None if (v is None or str(v).strip() == "") else str(v).strip() for v in row.tolist()]
            rows.append(cells)
        if rows:
            out.append(rows)
    return out
