from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pdf_table_pipeline.keywords import normalize_text

if TYPE_CHECKING:
    from pdf_table_pipeline.config import ExtractionConfig


def _looks_numeric_cell(s: str | None) -> bool:
    if s is None or not str(s).strip():
        return False
    t = re.sub(r"[\s,]", "", str(s))
    return bool(re.match(r"^[\d.]+[a-zA-Z%]*$", t) or re.match(r"^[\d.,]+[kK]?[aA]?$", t))


def join_multiline_in_cell(cell: str | None, joiner: str) -> str | None:
    if cell is None:
        return None
    s = str(cell)
    if "\n" not in s and "\r" not in s:
        return s.strip() or None
    parts = re.split(r"[\r\n]+", s)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return None
    return joiner.join(parts)


def clean_rows(
    rows: list[list[str | None]],
    config: ExtractionConfig,
) -> tuple[list[list[str | None]], list[str]]:
    """Multiline join, optional forward-fill, header synonym map, trim; return warnings."""
    warnings: list[str] = []
    if not rows:
        return [], warnings

    joiner = config.multiline_join
    cleaned: list[list[str | None]] = []
    for row in rows:
        cleaned.append([join_multiline_in_cell(c, joiner) for c in row])

    max_cols = max(len(r) for r in cleaned)
    col_counts = [len(r) for r in cleaned]
    if len(set(col_counts)) > 1:
        warnings.append("column_count_varies_across_rows")

    # Forward-fill configured columns (do not treat numeric cells as category seeds)
    ff = set(config.forward_fill_column_indices)
    if ff:
        fill_state: dict[int, str | None] = {j: None for j in ff}
        for row in cleaned:
            while len(row) < max_cols:
                row.append(None)
            for j in ff:
                if j >= len(row):
                    continue
                cell = row[j]
                if cell is not None and str(cell).strip():
                    s = str(cell).strip()
                    if config.never_forward_fill_numeric_columns and _looks_numeric_cell(s):
                        continue
                    fill_state[j] = s
                else:
                    if fill_state[j] is not None:
                        row[j] = fill_state[j]

    # Apply header synonyms on first row only (display / slug consistency)
    if config.header_synonyms and cleaned:
        hdr = cleaned[0]
        new_hdr: list[str | None] = []
        for c in hdr:
            if c is None:
                new_hdr.append(None)
                continue
            n = normalize_text(str(c))
            replaced = str(c).strip()
            for sub, slug in config.header_synonyms.items():
                if normalize_text(sub) in n:
                    replaced = slug
                    break
            new_hdr.append(replaced)
        cleaned[0] = new_hdr

    cleaned = _drop_empty_trailing_rows(cleaned)
    cleaned = _drop_empty_trailing_columns(cleaned)

    # Re-check column lengths
    if cleaned:
        mc = max(len(r) for r in cleaned)
        for i, row in enumerate(cleaned):
            if len(row) != mc:
                warnings.append(f"row_{i}_col_count_mismatch")
                break

    return cleaned, warnings


def rows_to_headers_data(
    rows: list[list[str | None]],
    header_rows: int = 1,
) -> tuple[list[str], list[list[str | None]]]:
    """First `hr` header lines merged per column; remaining rows are data."""
    if not rows:
        return [], []
    n = len(rows)
    if n == 1:
        hr = 1
    else:
        hr = min(max(1, header_rows), n - 1)
    max_cols = max(len(r) for r in rows[:hr])
    headers: list[str] = []
    for col in range(max_cols):
        parts: list[str] = []
        for r in range(hr):
            row = rows[r]
            if col < len(row) and row[col]:
                parts.append(str(row[col]).strip())
        headers.append(" | ".join(parts) if parts else f"col_{col}")

    data = []
    for row in rows[hr:]:
        data.append(list(row))
    return headers, data


def _drop_empty_trailing_rows(rows: list[list[str | None]]) -> list[list[str | None]]:
    while rows:
        last = rows[-1]
        if all(c is None or not str(c).strip() for c in last):
            rows = rows[:-1]
        else:
            break
    return rows


def _drop_empty_trailing_columns(rows: list[list[str | None]]) -> list[list[str | None]]:
    if not rows:
        return rows
    max_cols = max(len(r) for r in rows)
    empty_tail = 0
    for col in range(max_cols - 1, -1, -1):
        col_empty = True
        for row in rows:
            if col < len(row) and row[col] is not None and str(row[col]).strip():
                col_empty = False
                break
        if col_empty:
            empty_tail += 1
        else:
            break
    if empty_tail == 0:
        return rows
    new_max = max_cols - empty_tail
    return [r[:new_max] for r in rows]
