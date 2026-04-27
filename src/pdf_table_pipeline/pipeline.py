from __future__ import annotations

import logging
import re
from pathlib import Path

import pdfplumber

from pdf_table_pipeline.clean import clean_rows, rows_to_headers_data
from pdf_table_pipeline.config import ExtractionConfig
from pdf_table_pipeline.extract_camelot_optional import extract_tables_camelot_stream_page
from pdf_table_pipeline.extract_pdfplumber import (
    PdfPlumberTableCandidate,
    extract_tables_pdfplumber_page,
)
from pdf_table_pipeline.keywords import page_keyword_score, table_keyword_match
from pdf_table_pipeline.models import ExtractedTable, ExtractionResult
from pdf_table_pipeline.qa_words import word_alignment_confidence

logger = logging.getLogger(__name__)


def extract_keyword_tables(
    pdf_path: str | Path,
    config: ExtractionConfig | None = None,
    target_pages: set[int] | None = None,
    apply_keyword_filter: bool = True,
) -> ExtractionResult:
    """
    End-to-end: score pages, extract tables (pdfplumber + optional Camelot),
    filter by keywords, clean, optional word QA, return structured result.
    """
    config = config or ExtractionConfig()
    pdf_path = Path(pdf_path)
    source = str(pdf_path.name)

    tables_out: list[ExtractedTable] = []
    skipped_pages: list[int] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_index_0, page in enumerate(pdf.pages):
            page_1based = page_index_0 + 1
            if target_pages is not None and page_1based not in target_pages:
                continue
            page_text = page.extract_text() or ""
            score, _hits = page_keyword_score(page_text, config)

            if apply_keyword_filter and config.use_page_prefilter and score < config.page_score_min:
                skipped_pages.append(page_1based)
                continue

            candidates = extract_tables_pdfplumber_page(page, page_1based, config)

            if not candidates and config.use_camelot_fallback:
                camelot_rows_list = extract_tables_camelot_stream_page(
                    pdf_path, page_1based, config
                )
                w, h = float(page.width), float(page.height)
                bbox_page = (0.0, 0.0, w, h)
                for idx, rows in enumerate(camelot_rows_list):
                    candidates.append(
                        PdfPlumberTableCandidate(
                            page_1based=page_1based,
                            page_index_0=page_index_0,
                            table_index=idx,
                            bbox=bbox_page,
                            rows=rows,
                            extractor="camelot_stream",
                        )
                    )

            accepted_on_page = 0
            for cand in candidates:
                cleaned, clean_warnings = clean_rows(cand.rows, config)
                matched_kw: list[str] = []
                if apply_keyword_filter:
                    ok, matched_kw, _mode = table_keyword_match(cleaned, config)
                    if not ok:
                        continue

                accepted_on_page += 1
                tid = f"p{page_1based:03d}_t{accepted_on_page:02d}"

                headers, data_rows = rows_to_headers_data(cleaned, config.header_row_count)
                if config.drop_tables_with_no_data_rows and not data_rows:
                    continue
                warnings = list(clean_warnings)

                conf = 0.75
                if config.run_word_qa:
                    conf = word_alignment_confidence(page, cand.bbox, cleaned, config)
                if warnings:
                    conf = max(0.0, conf - 0.05 * min(len(warnings), 3))

                et = ExtractedTable(
                    table_id=tid,
                    page=page_1based,
                    page_index_0=page_index_0,
                    keywords_matched=matched_kw,
                    match_mode=config.match_mode,
                    extractor=cand.extractor,
                    confidence=round(conf, 4),
                    headers=headers,
                    rows=data_rows,
                    warnings=warnings,
                    bbox=cand.bbox,
                    page_score=score,
                )
                tables_out.append(et)

    return ExtractionResult(
        source_pdf=source,
        tables=tables_out,
        skipped_pages=skipped_pages,
    )


def tables_to_long_dataframe(result: ExtractionResult):
    """Flatten to long form: table_id, page, row_index, col_name, value."""
    import pandas as pd

    records: list[dict] = []
    for t in result.tables:
        for ri, row in enumerate(t.rows):
            for ci, val in enumerate(row):
                col_name = t.headers[ci] if ci < len(t.headers) else f"col_{ci}"
                records.append(
                    {
                        "table_id": t.table_id,
                        "page": t.page,
                        "row_index": ri,
                        "col_name": col_name,
                        "value": val,
                    }
                )
    return pd.DataFrame.from_records(records)


def _unique_header_keys(headers: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    keys: list[str] = []
    for hi, h in enumerate(headers):
        base = (h.strip() if h else "") or f"col_{hi}"
        n = counts.get(base, 0)
        counts[base] = n + 1
        keys.append(base if n == 0 else f"{base}__{n}")
    return keys


def tables_to_wide_dataframe(result: ExtractionResult):
    """One row per data row with table_id, page, and dynamic columns from headers."""
    import pandas as pd

    rows: list[dict] = []
    for t in result.tables:
        keys = _unique_header_keys(t.headers)
        for ri, row in enumerate(t.rows):
            d: dict = {
                "table_id": t.table_id,
                "page": t.page,
                "row_index": ri,
            }
            for ci, key in enumerate(keys):
                d[key] = row[ci] if ci < len(row) else None
            rows.append(d)
    return pd.DataFrame(rows)


def extract_catalog_price_dataframe(
    result: ExtractionResult,
    catalog_regex: str = r"(?i)^[A-Z0-9_]{5,}$",
    price_regex: str = r"^\d[\d,]*(?:\.\d+)?$",
    max_price_lookahead: int = 2,
):
    """
    Return tidy rows of catalog/price pairs from extracted tables.

    Useful for 4-column patterns like:
    [catalog_3pole, price_3pole, catalog_4pole, price_4pole]
    """
    import pandas as pd

    cat_re = re.compile(catalog_regex)
    price_re = re.compile(price_regex)
    records: list[dict] = []
    def looks_strict_catalog(token: str) -> bool:
        # Catalog can be pure numeric (e.g. 28900) OR mixed alphanumeric.
        if not token:
            return False
        if token.isdigit():
            return True
        return any(ch.isalpha() for ch in token) and any(ch.isdigit() for ch in token)

    def build_ref_mrp_pairs(headers: list[str]) -> list[tuple[int, int]]:
        pairs: list[tuple[int, int]] = []
        ref_cols: list[int] = []
        mrp_cols: list[int] = []
        ref_tail_re = re.compile(r"\breference\b[\]\)]*$", re.IGNORECASE)
        mrp_tail_re = re.compile(r"\bmrp\b(?:\s*\[[^\]]*\])?[\]\)]*$", re.IGNORECASE)

        def _header_segments(text: str) -> list[str]:
            # Headers can be merged like "X | Three Pole Reference"
            return [seg.strip() for seg in text.split("|") if seg.strip()]

        def _is_reference_header(text: str) -> bool:
            if not text:
                return False
            segs = _header_segments(text)
            return any(ref_tail_re.search(seg.lower()) for seg in segs)

        def _is_mrp_header(text: str) -> bool:
            if not text:
                return False
            segs = _header_segments(text)
            return any(mrp_tail_re.search(seg.lower()) for seg in segs)

        for idx, h in enumerate(headers):
            hl = str(h or "").strip().lower()
            if _is_reference_header(hl):
                ref_cols.append(idx)
            if _is_mrp_header(hl):
                mrp_cols.append(idx)
        if not ref_cols or not mrp_cols:
            return pairs

        # Pair each reference column with nearest MRP column to its right (preferred).
        used_mrp: set[int] = set()
        for ref_idx in ref_cols:
            right_candidates = [m for m in mrp_cols if m > ref_idx and m not in used_mrp]
            if right_candidates:
                chosen = min(right_candidates, key=lambda m: m - ref_idx)
            else:
                remaining = [m for m in mrp_cols if m not in used_mrp]
                if not remaining:
                    continue
                chosen = min(remaining, key=lambda m: abs(m - ref_idx))
            used_mrp.add(chosen)
            pairs.append((ref_idx, chosen))
        return pairs

    for t in result.tables:
        ref_mrp_pairs = build_ref_mrp_pairs(t.headers)
        if not ref_mrp_pairs:
            # Per user rule: only process tables where headers map Reference -> MRP.
            continue
        for ri, row in enumerate(t.rows):
            row_values = [str(v).strip() if v is not None else "" for v in row]

            # Preferred path: Reference -> MRP mapping from table headers
            if ref_mrp_pairs:
                for ref_idx, mrp_idx in ref_mrp_pairs:
                    if ref_idx >= len(row_values):
                        continue
                    ref_val = row_values[ref_idx]
                    if not ref_val:
                        continue
                    normalized_catalog = re.sub(r"[^A-Za-z0-9_]", "", ref_val).upper()
                    if (
                        not normalized_catalog
                        or not cat_re.match(normalized_catalog)
                        or not looks_strict_catalog(normalized_catalog)
                    ):
                        continue
                    if mrp_idx >= len(row_values):
                        continue
                    mrp_val = row_values[mrp_idx]
                    if not mrp_val:
                        continue
                    compact = mrp_val.replace(" ", "").replace(",", "")
                    if not price_re.match(compact):
                        continue
                    if compact.isdigit() and len(compact) < 3:
                        continue
                    records.append(
                        {
                            "page": t.page,
                            "table_id": t.table_id,
                            "row_index": ri,
                            "catalog_no": normalized_catalog,
                            "price": compact,
                            "price_raw": mrp_val,
                            "catalog_col": ref_idx,
                            "price_col": mrp_idx,
                            "pole_hint": "",
                        }
                    )
                # When header mapping exists, avoid fallback scanning to reduce noise.
                continue

            # No generic fallback scan here by design.

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df = df.drop_duplicates(subset=["page", "table_id", "row_index", "catalog_no", "price"])
        df = df.sort_values(by=["page", "table_id", "row_index", "catalog_col"]).reset_index(drop=True)
    return df


def extract_catalog_price_from_pdf_text(
    pdf_path: str | Path,
    target_pages: set[int] | None = None,
    catalog_regex: str = r"(?i)^[A-Z0-9_]{5,}$",
    price_regex: str = r"^\d[\d,]*(?:\.\d+)?$",
):
    """
    Direct catalog/price extraction from page text lines.

    This is useful when table structure extraction is noisy on some pages.
    """
    import pandas as pd

    cat_re = re.compile(catalog_regex)
    price_re = re.compile(price_regex)
    token_re = re.compile(r"[A-Za-z0-9_]{5,}|\d[\d,]*(?:\.\d+)?")

    def looks_catalog(token: str) -> bool:
        t = re.sub(r"[^A-Za-z0-9_]", "", token).upper()
        if not t or not cat_re.match(t):
            return False
        # Text fallback is intentionally stricter to avoid numeric noise;
        # pure numeric catalogs are primarily expected from structured Reference columns.
        return any(ch.isalpha() for ch in t) and any(ch.isdigit() for ch in t)

    records: list[dict] = []
    pdf_path = Path(pdf_path)
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pidx, page in enumerate(pdf.pages, start=1):
            if target_pages is not None and pidx not in target_pages:
                continue
            text = page.extract_text() or ""
            for line_idx, line in enumerate(text.splitlines()):
                tokens = token_re.findall(line)
                for i, tok in enumerate(tokens):
                    if not looks_catalog(tok):
                        continue
                    catalog_no = re.sub(r"[^A-Za-z0-9_]", "", tok).upper()
                    price_val: str | None = None
                    for j in range(i + 1, min(i + 4, len(tokens))):
                        cand = tokens[j]
                        compact = cand.replace(",", "")
                        if price_re.match(compact):
                            if compact.isdigit() and len(compact) < 3:
                                continue
                            price_val = compact
                            break
                    if price_val is None:
                        continue
                    records.append(
                        {
                            "page": pidx,
                            "table_id": f"p{pidx:03d}_text",
                            "row_index": line_idx,
                            "catalog_no": catalog_no,
                            "price": price_val,
                            "price_raw": price_val,
                            "catalog_col": None,
                            "price_col": None,
                            "pole_hint": "",
                        }
                    )

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df = df.drop_duplicates(subset=["page", "catalog_no", "price"])
        df = df.sort_values(by=["page", "row_index", "catalog_no"]).reset_index(drop=True)
    return df


def merge_catalog_price_results(*dfs):
    """Union multiple catalog/price DataFrames and deduplicate."""
    import pandas as pd

    valid = [df for df in dfs if df is not None and not df.empty]
    if not valid:
        return pd.DataFrame(columns=["page", "catalog_no", "price"])
    out = pd.concat(valid, ignore_index=True)
    out = out.drop_duplicates(subset=["page", "catalog_no", "price"])
    out = out.sort_values(by=["page", "catalog_no", "price"]).reset_index(drop=True)
    return out


def format_catalog_price_output(df):
    """
    Keep only user-facing columns for export/display.

    Output columns: page, catalog_no, price
    """
    import pandas as pd

    wanted = ["page", "catalog_no", "price"]
    if df is None or df.empty:
        return pd.DataFrame(columns=wanted)
    out = df.copy()
    for col in wanted:
        if col not in out.columns:
            out[col] = None
    out = out[wanted]
    out = out.drop_duplicates(subset=wanted).sort_values(by=["page", "catalog_no", "price"])
    out = out.reset_index(drop=True)
    return out
