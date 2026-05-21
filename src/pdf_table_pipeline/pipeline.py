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
    ref_tail_re = re.compile(r"\breference\b[\]\)]*$", re.IGNORECASE)
    order_code_header_re = re.compile(r"(?i)order(?:ing)?\s*code\b")
    mrp_tail_re = re.compile(
        r"\b(?:mrp|lp)\b(?:\s*\[[^\]]*\])?[\]\)]*$",
        re.IGNORECASE,
    )
    # ABB-style "Unit MRP (₹)", "L.P.(₹)", list price.
    mrp_header_relaxed_re = re.compile(
        r"(?i)\b(?:m\s*\.?\s*r\s*\.?\s*p|list\s*price|l\.\s*p\.?|l\.p\.)\b"
    )

    def parse_price_from_row(
        row_values: list[str],
        mrp_idx: int,
        price_header: str,
    ) -> tuple[str | None, str | None]:
        """
        Parse price-like value from the mapped MRP/LP column.
        Returns (normalized_price, raw_price_text).

        Handles fragmented tokens such as:
        - "On R" + "equ" + "est" -> "On Request"
        - "1,2" + "34" -> "1,234"
        """
        if 0 <= mrp_idx < len(row_values):
            raw_cell = (row_values[mrp_idx] or "").strip()
            if raw_cell:
                compact_cell = raw_cell.replace(",", "").replace(" ", "")
                if price_re.match(compact_cell) and not (
                    compact_cell.isdigit() and len(compact_cell) < 3
                ):
                    return compact_cell, raw_cell
        start_candidates = [mrp_idx, mrp_idx - 1, mrp_idx + 1]
        checked: set[int] = set()
        is_lp_price_col = " lp" in f" {price_header}" or price_header.endswith("lp")
        for start_idx in start_candidates:
            if start_idx in checked or start_idx < 0 or start_idx >= len(row_values):
                continue
            checked.add(start_idx)

            base = (row_values[start_idx] or "").strip()
            if not base:
                continue

            fragments: list[str] = [base]
            for step in range(1, max_price_lookahead + 1):
                nx = start_idx + step
                if nx >= len(row_values):
                    break
                nxt = (row_values[nx] or "").strip()
                if not nxt:
                    break
                fragments.append(nxt)

            joined_space = " ".join(fragments).strip()
            joined_compact = "".join(fragments).replace(" ", "")

            is_on_request = "onrequest" in joined_compact.lower()
            if is_lp_price_col or is_on_request:
                if is_on_request:
                    return "On Request", joined_space
                return base, base

            # Numeric price can be split in adjacent columns, so validate compact form.
            compact_numeric = joined_compact.replace(",", "")
            if price_re.match(compact_numeric):
                if compact_numeric.isdigit() and len(compact_numeric) < 3:
                    continue
                return compact_numeric, joined_space
        return None, None

    def looks_strict_catalog(token: str) -> bool:
        # Catalog can be pure numeric (e.g. 28900) OR mixed alphanumeric.
        if not token:
            return False
        if token.isdigit():
            return True
        return any(ch.isalpha() for ch in token) and any(ch.isdigit() for ch in token)

    def is_probable_order_code(token: str) -> bool:
        """ABB/Schneider order codes — not Type names like PSE18-600-70."""
        if not token or not cat_re.match(token):
            return False
        if token.isdigit():
            return len(token) >= 5
        # Type / product family (PSE, PSTX, XT2, …) — not order codes.
        if re.match(r"^[A-Z]{2,5}\d", token) and not token.startswith("1S"):
            return False
        if re.match(r"^1S[A-Z]{2}\d", token):
            return "R" in token and len(token) >= 12
        return looks_strict_catalog(token)

    def extract_order_code_from_row(
        row_values: list[str], col_idx: int
    ) -> str | None:
        """One order code per row; join when pdfplumber splits R7000 into R700 + 0."""
        if col_idx >= len(row_values):
            return None
        base = str(row_values[col_idx] or "").strip()
        if not base:
            return None
        base = re.sub(r"[\s\u25a0\u25aa■]+$", "", base)
        base = re.sub(r"\s+n\s*$", "", base, flags=re.I)
        nc = re.sub(r"[^A-Z0-9]", "", base.upper())
        if col_idx + 1 < len(row_values):
            nxt = str(row_values[col_idx + 1] or "").strip()
            if (
                nxt
                and re.fullmatch(r"\d{1,2}", nxt)
                and re.match(r"^1S[A-Z]{2}", nc)
                and "R" in nc
            ):
                nc = nc + nxt
        return nc if is_probable_order_code(nc) else None

    def table_order_code_score(table: ExtractedTable) -> float:
        if not table.rows:
            return 0.0
        ncols = max(len(table.headers), max(len(r) for r in table.rows))
        best = 0.0
        for col in range(ncols):
            hits = 0
            for row in table.rows:
                rv = [str(v).strip() if v is not None else "" for v in row]
                if extract_order_code_from_row(rv, col):
                    hits += 1
            best = max(best, hits / len(table.rows))
        return best

    def tables_for_extraction(tables: list[ExtractedTable]) -> list[ExtractedTable]:
        """Prefer compact product tables; skip full-page fragmented layouts."""
        by_page: dict[int, list[ExtractedTable]] = {}
        for t in tables:
            by_page.setdefault(t.page, []).append(t)
        chosen: list[ExtractedTable] = []
        for page_tables in by_page.values():
            scored = [(t, table_order_code_score(t), len(t.headers)) for t in page_tables]
            narrow_good = [
                (t, sc)
                for t, sc, ncol in scored
                if sc >= 0.35 and ncol <= 7
            ]
            if narrow_good:
                chosen.extend(t for t, _ in narrow_good)
                continue
            best_sc = max(sc for _, sc, _ in scored)
            for t, sc, ncol in scored:
                if sc >= max(0.2, best_sc * 0.5) and ncol <= 12:
                    chosen.append(t)
        return chosen

    def build_ref_mrp_pairs(headers: list[str]) -> list[tuple[int, int]]:
        pairs: list[tuple[int, int]] = []
        ref_cols: list[int] = []
        mrp_cols: list[int] = []

        def _header_segments(text: str) -> list[str]:
            # Headers can be merged like "X | Three Pole Reference"
            return [seg.strip() for seg in text.split("|") if seg.strip()]

        def _is_reference_header(text: str) -> bool:
            if not text:
                return False
            segs = _header_segments(text)
            for seg in segs:
                sl = seg.lower()
                if ref_tail_re.search(sl) or order_code_header_re.search(seg):
                    return True
            return False

        def _is_mrp_header(text: str) -> bool:
            if not text:
                return False
            segs = _header_segments(text)
            for seg in segs:
                sl = seg.lower()
                if mrp_header_relaxed_re.search(seg) or mrp_tail_re.search(sl):
                    return True
            return False

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

    def infer_ref_mrp_pairs_from_rows(rows: list[list[str | None]]) -> tuple[list[tuple[int, int]], set[int]]:
        """
        Find a row that contains header-like labels ending in Reference/MRP and infer column pairs.
        Returns (pairs, header_row_indices).
        """
        for ridx, row in enumerate(rows):
            vals = [str(v or "").strip().lower() for v in row]
            ref_cols = [
                i
                for i, v in enumerate(vals)
                if ref_tail_re.search(v) or order_code_header_re.search(v)
            ]
            mrp_cols = [
                i
                for i, v in enumerate(vals)
                if mrp_header_relaxed_re.search(v) or mrp_tail_re.search(v)
            ]
            if not ref_cols or not mrp_cols:
                continue
            pairs: list[tuple[int, int]] = []
            used: set[int] = set()
            for r in ref_cols:
                rights = [m for m in mrp_cols if m > r and m not in used]
                if rights:
                    m = min(rights, key=lambda x: x - r)
                else:
                    rem = [m for m in mrp_cols if m not in used]
                    if not rem:
                        continue
                    m = min(rem, key=lambda x: abs(x - r))
                used.add(m)
                pairs.append((r, m))
            if pairs:
                return pairs, {ridx}
        return [], set()

    def infer_ref_mrp_pairs_by_content(
        rows: list[list[str | None]],
        headers: list[str] | None = None,
    ) -> list[tuple[int, int]]:
        """When headers are data (no 'Ordering code' label), find code + price columns."""
        if not rows:
            return []
        ncols = max(
            len(headers or []),
            max(len(r) for r in rows),
        )
        code_rates: list[float] = []
        for col in range(ncols):
            hits = 0
            for row in rows:
                rv = [str(v).strip() if v is not None else "" for v in row]
                if extract_order_code_from_row(rv, col):
                    hits += 1
            code_rates.append(hits / len(rows))

        if max(code_rates, default=0) < 0.15:
            return []

        ref_idx = max(range(ncols), key=lambda c: code_rates[c])
        mrp_idx: int | None = None
        for col in range(ref_idx + 1, ncols):
            price_hits = 0
            for row in rows:
                rv = [str(v).strip() if v is not None else "" for v in row]
                blob = " ".join(rv[col : min(col + 3, len(rv))])
                low = blob.lower().replace(" ", "")
                if "upon" in low and "request" in low:
                    price_hits += 1
                    continue
                compact = re.sub(r"[^0-9,.]", "", blob).replace(",", "")
                if compact and price_re.match(compact) and not (
                    compact.isdigit() and len(compact) < 3
                ):
                    price_hits += 1
            if price_hits >= max(1, len(rows) * 0.08):
                mrp_idx = col
                break
        if mrp_idx is None and headers:
            for col in range(ref_idx + 1, ncols):
                if col < len(headers) and parse_prices_from_mrp_blob(str(headers[col] or "")):
                    mrp_idx = col
                    break
        if mrp_idx is None:
            return []
        return [(ref_idx, mrp_idx)]

    def extract_catalog_tokens_from_blob(text: str) -> list[str]:
        """Catalog codes from a cell or merged header (pipe / whitespace separated)."""
        found: list[str] = []
        seen: set[str] = set()
        for seg in re.split(r"[|\n]+", str(text or "")):
            seg = seg.strip()
            if not seg or order_code_header_re.search(seg):
                continue
            if re.fullmatch(r"(?i)ordering\s*code\*?", seg.replace(" ", "")):
                continue
            for m in re.finditer(r"\b[A-Za-z0-9][A-Za-z0-9_]{4,}\b", seg):
                raw = m.group(0).strip()
                raw = re.sub(r"[\s\u25a0\u25aa■]+$", "", raw)
                raw = re.sub(r"\s+n\s*$", "", raw, flags=re.I)
                nc = re.sub(r"[^A-Za-z0-9_]", "", raw).upper()
                if nc and is_probable_order_code(nc) and nc not in seen:
                    seen.add(nc)
                    found.append(nc)
        return found

    def parse_prices_from_mrp_blob(header: str) -> list[str]:
        """All prices in a merged LP header (pipe-separated) or Upon request."""
        prices: list[str] = []
        for seg in re.split(r"[|\n]+", str(header or "")):
            seg = seg.strip()
            if not seg:
                continue
            low_joined = seg.lower().replace(" ", "")
            if "uponrequest" in low_joined or (
                "upon" in seg.lower() and "request" in seg.lower()
            ):
                prices.append("On Request")
                continue
            compact = re.sub(r"[^0-9,.]", "", seg).replace(",", "")
            if not compact or not price_re.match(compact):
                continue
            if compact.isdigit() and len(compact) < 3:
                continue
            prices.append(compact)
        return prices

    def parse_price_from_header_blob(header: str) -> str | None:
        """First price in merged MRP/LP header (numeric or On Request)."""
        parsed = parse_prices_from_mrp_blob(header)
        return parsed[0] if parsed else None

    def fill_prices_by_price_anchors(
        explicit: list[str | None],
        header_price: str | None = None,
    ) -> list[str | None]:
        """
        Fill merged MRP/LP cells without bleeding the next group's price upward.

        - Rows before the first in-row price use header_price when present.
        - Each explicit price applies from its row through the row before the next price.
        - Rows above the first explicit price in a block share that block's price.
        """
        n = len(explicit)
        filled: list[str | None] = [None] * n
        price_rows = [i for i, p in enumerate(explicit) if p]

        if not price_rows:
            if header_price:
                return [header_price] * n
            return filled

        if header_price:
            for i in range(0, price_rows[0]):
                filled[i] = header_price

        for idx, pr in enumerate(price_rows):
            start = 0 if idx == 0 and not header_price else pr
            end = (price_rows[idx + 1] - 1) if idx + 1 < len(price_rows) else (n - 1)
            block_price = explicit[pr]
            for i in range(start, end + 1):
                filled[i] = block_price
        return filled

    for t in tables_for_extraction(result.tables):
        ref_mrp_pairs = build_ref_mrp_pairs(t.headers)
        skip_rows: set[int] = set()
        if not ref_mrp_pairs:
            ref_mrp_pairs, skip_rows = infer_ref_mrp_pairs_from_rows(t.rows)
        if not ref_mrp_pairs:
            ref_mrp_pairs = infer_ref_mrp_pairs_by_content(t.rows, t.headers)
        if not ref_mrp_pairs:
            continue
        nrows = len(t.rows)
        for ref_idx, mrp_idx in ref_mrp_pairs:
            row_catalogs: list[list[str]] = [[] for _ in range(nrows)]
            explicit_prices: list[str | None] = [None] * nrows
            price_raw_cells: list[str] = [""] * nrows

            ref_header_blob = (
                str(t.headers[ref_idx] or "") if ref_idx < len(t.headers) else ""
            )
            mrp_header_blob = (
                str(t.headers[mrp_idx] or "") if mrp_idx < len(t.headers) else ""
            )
            header_prices = parse_prices_from_mrp_blob(mrp_header_blob)
            header_catalogs = extract_catalog_tokens_from_blob(ref_header_blob)
            header_price = (
                header_prices[0]
                if len(header_prices) == 1
                else ("On Request" if header_prices == ["On Request"] else None)
            )
            header_price_raw = mrp_header_blob

            if len(header_catalogs) == len(header_prices) and header_catalogs:
                for hc, hp in zip(header_catalogs, header_prices, strict=True):
                    records.append(
                        {
                            "page": t.page,
                            "table_id": t.table_id,
                            "row_index": -1,
                            "catalog_no": hc,
                            "price": hp,
                            "price_raw": hp,
                            "catalog_col": ref_idx,
                            "price_col": mrp_idx,
                            "pole_hint": "",
                        }
                    )
            elif header_catalogs and header_price:
                for header_catalog in header_catalogs:
                    records.append(
                        {
                            "page": t.page,
                            "table_id": t.table_id,
                            "row_index": -1,
                            "catalog_no": header_catalog,
                            "price": header_price,
                            "price_raw": header_price_raw or header_price,
                            "catalog_col": ref_idx,
                            "price_col": mrp_idx,
                            "pole_hint": "",
                        }
                    )

            for ri, row in enumerate(t.rows):
                if ri in skip_rows:
                    continue
                row_values = [str(v).strip() if v is not None else "" for v in row]
                oc = extract_order_code_from_row(row_values, ref_idx)
                if oc:
                    row_catalogs[ri] = [oc]
                if mrp_idx < len(row_values):
                    price_header = mrp_header_blob.lower()
                    np, praw = parse_price_from_row(
                        row_values, mrp_idx, price_header
                    )
                    if np:
                        explicit_prices[ri] = np
                        price_raw_cells[ri] = (praw or row_values[mrp_idx] or "")

            filled_prices = fill_prices_by_price_anchors(
                explicit_prices, header_price=header_price
            )

            for ri in range(nrows):
                if ri in skip_rows:
                    continue
                if not row_catalogs[ri] or not filled_prices[ri]:
                    continue
                raw_disp = price_raw_cells[ri] or str(
                    (t.rows[ri][mrp_idx] if mrp_idx < len(t.rows[ri]) else "") or ""
                )
                for catalog_no in row_catalogs[ri]:
                    records.append(
                        {
                            "page": t.page,
                            "table_id": t.table_id,
                            "row_index": ri,
                            "catalog_no": catalog_no,
                            "price": filled_prices[ri],
                            "price_raw": raw_disp or filled_prices[ri],
                            "catalog_col": ref_idx,
                            "price_col": mrp_idx,
                            "pole_hint": "",
                        }
                    )

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df = df.drop_duplicates(subset=["page", "table_id", "row_index", "catalog_no", "price"])
        df = df.sort_values(by=["page", "table_id", "row_index", "catalog_col"]).reset_index(drop=True)
    return df


def extract_catalog_price_dataframe_l_and_t(
    result: ExtractionResult,
    catalog_regex: str = r"(?i)^[A-Z0-9_]{5,}$",
    price_regex: str = r"^\d[\d,]*(?:\.\d+)?$",
):
    """
    L&T-style price lists: headers use *Cat. No.* / *Catalog* and *M.R.P.* (not "Reference"),
    often with multiple catalog columns and multiple MRP columns (ampere tiers) per row.
    """
    import pandas as pd

    cat_re = re.compile(catalog_regex)
    price_re = re.compile(price_regex)
    cat_header_re = re.compile(
        r"(?i)cat\.?\s*no\.?|catalog(?:ue)?\s*(?:no|number|#)|\bcat\s*#\b"
    )
    mrp_header_re = re.compile(r"(?i)m\s*\.?\s*r\s*\.?\s*p|list\s*price|unit\s*price")

    records: list[dict] = []

    def parse_price_cell(
        row_values: list[str],
        mrp_idx: int,
    ) -> tuple[str | None, str | None]:
        """
        L&T price lists place one MRP per cell; do not merge adjacent columns (avoids
        concatenating separate ampere-tier prices like 205|300|415 into 205300415).
        """
        if mrp_idx < 0 or mrp_idx >= len(row_values):
            return None, None
        raw = (row_values[mrp_idx] or "").strip()
        if not raw or raw in {"-", "—"}:
            return None, None
        if "on" in raw.lower() and "request" in raw.lower():
            return "On Request", raw
        compact = raw.replace(",", "").replace(" ", "")
        if price_re.match(compact):
            if compact.isdigit() and len(compact) < 3:
                return None, None
            return compact, raw
        return None, None

    def looks_strict_catalog(token: str) -> bool:
        if not token:
            return False
        if token.isdigit():
            return True
        return any(ch.isalpha() for ch in token) and any(ch.isdigit() for ch in token)

    def header_blob_segments(h: str) -> list[str]:
        return [seg.strip() for seg in str(h or "").split("|") if seg.strip()]

    def header_has_cat_no(h: str) -> bool:
        return any(cat_header_re.search(seg) for seg in header_blob_segments(h))

    def header_has_mrp(h: str) -> bool:
        return any(mrp_header_re.search(seg) for seg in header_blob_segments(h))

    def build_cat_mrp_pairs(headers: list[str]) -> list[tuple[int, int]]:
        ref_cols = [i for i, h in enumerate(headers) if header_has_cat_no(str(h or ""))]
        mrp_cols = [i for i, h in enumerate(headers) if header_has_mrp(str(h or ""))]
        if not ref_cols or not mrp_cols:
            return []
        pairs: list[tuple[int, int]] = []
        for r in ref_cols:
            for m in mrp_cols:
                if m > r:
                    pairs.append((r, m))
        return pairs

    def infer_pairs_from_rows(rows: list[list[str | None]]) -> tuple[list[tuple[int, int]], set[int]]:
        for ridx, row in enumerate(rows):
            vals = [str(v or "").strip().lower() for v in row]
            ref_cols = [i for i, v in enumerate(vals) if cat_header_re.search(v)]
            mrp_cols = [i for i, v in enumerate(vals) if mrp_header_re.search(v)]
            if not ref_cols or not mrp_cols:
                continue
            pairs: list[tuple[int, int]] = []
            for r in ref_cols:
                for m in mrp_cols:
                    if m > r:
                        pairs.append((r, m))
            if pairs:
                return pairs, {ridx}
        return [], set()

    for t in result.tables:
        ref_mrp_pairs = build_cat_mrp_pairs(t.headers)
        skip_rows: set[int] = set()
        if not ref_mrp_pairs:
            ref_mrp_pairs, skip_rows = infer_pairs_from_rows(t.rows)
        if not ref_mrp_pairs:
            continue
        for ri, row in enumerate(t.rows):
            if ri in skip_rows:
                continue
            row_values = [str(v).strip() if v is not None else "" for v in row]
            for ref_idx, mrp_idx in ref_mrp_pairs:
                if ref_idx >= len(row_values):
                    continue
                raw_ref = row_values[ref_idx]
                if not raw_ref or raw_ref in {"-", "—"}:
                    continue
                normalized_catalog = re.sub(r"[^A-Za-z0-9_]", "", raw_ref).upper()
                if not normalized_catalog or not cat_re.match(normalized_catalog):
                    continue
                if not looks_strict_catalog(normalized_catalog):
                    continue
                if mrp_idx >= len(row_values):
                    continue
                normalized_price, price_raw = parse_price_cell(row_values, mrp_idx)
                if not normalized_price:
                    continue
                records.append(
                    {
                        "page": t.page,
                        "table_id": t.table_id,
                        "row_index": ri,
                        "catalog_no": normalized_catalog,
                        "price": normalized_price,
                        "price_raw": price_raw or row_values[mrp_idx],
                        "catalog_col": ref_idx,
                        "price_col": mrp_idx,
                        "pole_hint": "",
                    }
                )

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
    *,
    allow_numeric_catalog: bool = False,
    strip_trailing_order_stock_markers: bool = False,
):
    """
    Direct catalog/price extraction from page text lines.

    This is useful when table structure extraction is noisy on some pages.
    strip_trailing_order_stock_markers: strip trailing stock marker " n" from tokens (ABB order codes).
    """
    import pandas as pd

    cat_re = re.compile(catalog_regex)
    price_re = re.compile(price_regex)
    token_re = re.compile(r"[A-Za-z0-9_]{5,}|\d[\d,]*(?:\.\d+)?")

    def looks_catalog(token: str) -> bool:
        t = re.sub(r"[^A-Za-z0-9_]", "", token).upper()
        if not t or not cat_re.match(t):
            return False
        if allow_numeric_catalog and t.isdigit():
            return len(t) >= 5
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
                pending_catalogs: list[str] = []
                for tok in tokens:
                    compact = tok.replace(",", "")
                    if price_re.match(compact):
                        if compact.isdigit() and len(compact) < 3:
                            continue
                        if pending_catalogs:
                            for catalog_no in pending_catalogs:
                                records.append(
                                    {
                                        "page": pidx,
                                        "table_id": f"p{pidx:03d}_text",
                                        "row_index": line_idx,
                                        "catalog_no": catalog_no,
                                        "price": compact,
                                        "price_raw": tok,
                                        "catalog_col": None,
                                        "price_col": None,
                                        "pole_hint": "",
                                    }
                                )
                            pending_catalogs = []
                        continue
                    if not looks_catalog(tok):
                        continue
                    raw_tok = tok
                    if strip_trailing_order_stock_markers:
                        raw_tok = re.sub(
                            r"\s+n\s*$", "", str(raw_tok).strip(), flags=re.I
                        )
                    catalog_no = re.sub(r"[^A-Za-z0-9_]", "", raw_tok).upper()
                    pending_catalogs.append(catalog_no)

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
    if "table_id" in out.columns:
        out["_prefer_struct"] = (
            ~out["table_id"].astype(str).str.endswith("_text")
        ).astype(int)
        out["_prefer_data_row"] = out.get("row_index", -1).fillna(-1).ge(0).astype(int)
        out = out.sort_values(
            by=["page", "catalog_no", "_prefer_struct", "_prefer_data_row", "row_index"],
            ascending=[True, True, False, False, True],
        )
        out = out.drop_duplicates(subset=["page", "catalog_no"], keep="first")
        out = out.drop(columns=["_prefer_struct", "_prefer_data_row"], errors="ignore")
    else:
        out = out.drop_duplicates(subset=["page", "catalog_no", "price"])
    out = out.sort_values(by=["page", "catalog_no", "price"]).reset_index(drop=True)
    return out


def extract_catalog_price_dataframe_siemens(
    result: ExtractionResult,
    catalog_regex: str = r"(?i)^[A-Z0-9][A-Z0-9_./-]{4,}$",
):
    """
    Siemens-oriented extraction from table rows.

    Siemens price lists often contain row patterns like:
    Type code (e.g. 3WJ1108-2AF52-1AA0) + Unit LP (e.g. 334220.-)
    without explicit Reference/MRP headers.
    """
    import pandas as pd

    cat_re = re.compile(catalog_regex)
    # Practical Siemens "Type" token: requires at least one '-' or '/' segment.
    token_catalog_re = re.compile(r"(?i)\b[A-Z0-9]{2,}(?:[-/][A-Z0-9.]{2,})+\b")
    # Siemens LP values are typically printed as "12345.-" / "1,23,456.-".
    token_price_re = re.compile(r"(?<!\d)(\d[\d,]*)\s*\.-")
    records: list[dict] = []
    
    def is_complete_siemens_catalog(token: str) -> bool:
        normalized = re.sub(r"[^A-Z0-9_./-]", "", token.upper())
        if not normalized or not cat_re.match(normalized):
            return False
        # Drop partial fragments like "2AF02" or "2AF02-1AA0":
        # valid full codes typically have a long product-family prefix
        # before first hyphen (e.g. 3WJ1108, 3WJ9111).
        if "-" not in normalized:
            return False
        first_segment = normalized.split("-", 1)[0]
        if len(first_segment) < 6:
            return False
        if not normalized[0].isdigit():
            return False
        if not any(ch.isalpha() for ch in first_segment):
            return False
        if not any(ch.isdigit() for ch in first_segment):
            return False
        return True

    for t in result.tables:
        for ri, row in enumerate(t.rows):
            if not row:
                continue
            tokens: list[tuple[str, str, int]] = []
            for ci, cell in enumerate(row):
                text = str(cell or "").strip()
                if not text:
                    continue

                for m in token_catalog_re.finditer(text):
                    cat = m.group(0).strip().upper()
                    normalized_catalog = re.sub(r"[^A-Z0-9_./-]", "", cat)
                    if not is_complete_siemens_catalog(normalized_catalog):
                        continue
                    tokens.append(("catalog", normalized_catalog, ci))

                for m in token_price_re.finditer(text):
                    raw_num = m.group(1)
                    normalized_price = raw_num.replace(",", "")
                    if not normalized_price.isdigit() or len(normalized_price) < 3:
                        continue
                    tokens.append(("price", normalized_price, ci))

            if not tokens:
                continue

            # Pair each catalog with nearest following price token.
            for idx, (kind, value, col_idx) in enumerate(tokens):
                if kind != "catalog":
                    continue
                price_val: str | None = None
                price_col: int | None = None
                for next_kind, next_value, next_col in tokens[idx + 1 :]:
                    if next_kind == "price":
                        price_val = next_value
                        price_col = next_col
                        break
                if price_val is None:
                    continue
                records.append(
                    {
                        "page": t.page,
                        "table_id": t.table_id,
                        "row_index": ri,
                        "catalog_no": value,
                        "price": price_val,
                        "price_raw": f"{price_val}.-",
                        "catalog_col": col_idx,
                        "price_col": price_col,
                        "pole_hint": "",
                    }
                )

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df = df.drop_duplicates(subset=["page", "catalog_no", "price"])
        df = df.sort_values(by=["page", "table_id", "row_index", "catalog_col"]).reset_index(drop=True)
    return df


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
