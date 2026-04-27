from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pdfplumber.page

    from pdf_table_pipeline.config import ExtractionConfig


def word_alignment_confidence(
    page: pdfplumber.page.Page,
    bbox: tuple[float, float, float, float],
    rows: list[list[str | None]],
    _config: ExtractionConfig,
) -> float:
    """
    Compare character coverage in extracted cells vs words in the same bbox.

    Returns a heuristic in [0, 1]; higher means better agreement.
    """
    try:
        crop = page.within_bbox(bbox)
    except Exception:
        return 0.5
    words = crop.extract_words(use_text_flow=True, keep_blank_chars=False, extra_attrs=[])
    table_chars = sum(len(str(c or "")) for row in rows for c in row)
    word_chars = sum(len(w.get("text", "")) for w in words)
    if table_chars == 0 and word_chars == 0:
        return 1.0
    if table_chars == 0 or word_chars == 0:
        return 0.4
    ratio = min(word_chars, table_chars) / max(word_chars, table_chars)

    col_counts = [len(r) for r in rows] if rows else []
    if col_counts and len(set(col_counts)) == 1:
        ratio = min(1.0, ratio + 0.05)

    return max(0.0, min(1.0, ratio))
