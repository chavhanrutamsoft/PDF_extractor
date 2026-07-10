#!/usr/bin/env python3
"""CLI extractor for cPanel PHP integration."""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pdf_table_pipeline import pipeline as pipeline_mod
from pdf_table_pipeline.config import ExtractionConfig

extract_catalog_price_dataframe = pipeline_mod.extract_catalog_price_dataframe
extract_catalog_price_dataframe_l_and_t = getattr(
    pipeline_mod,
    "extract_catalog_price_dataframe_l_and_t",
    extract_catalog_price_dataframe,
)
extract_catalog_price_dataframe_siemens = getattr(
    pipeline_mod, "extract_catalog_price_dataframe_siemens", None
)
is_complete_siemens_catalog = getattr(pipeline_mod, "is_complete_siemens_catalog", None)
_extract_catalog_price_from_pdf_text_raw = pipeline_mod.extract_catalog_price_from_pdf_text
SIEMENS_FULL_CATALOG_REGEX = getattr(
    pipeline_mod,
    "SIEMENS_FULL_CATALOG_REGEX",
    r"(?i)^(?:\d[A-Z0-9]{5,}(?:-[A-Z0-9.]{1,})+|\d[A-Z]{2,}[A-Z0-9]{4,})$",
)


def extract_catalog_price_from_pdf_text(pdf_path, **kwargs):
    fn = _extract_catalog_price_from_pdf_text_raw
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(pdf_path, **kwargs)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return fn(pdf_path, **kwargs)
    allowed = set(sig.parameters)
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    return fn(pdf_path, **filtered)


extract_keyword_tables = pipeline_mod.extract_keyword_tables
format_catalog_price_output = pipeline_mod.format_catalog_price_output
merge_catalog_price_results = pipeline_mod.merge_catalog_price_results

SCHNEIDER_CLIENT = "Schneider"
SIEMENS_CLIENT = "Siemens"
ABB_CLIENT = "ABB"
L_AND_T_CLIENT = "L And T"
SUPPORTED_CLIENTS = {SCHNEIDER_CLIENT, SIEMENS_CLIENT, ABB_CLIENT, L_AND_T_CLIENT}


def parse_pages_spec(spec: str) -> set[int]:
    out: set[int] = set()
    chunks = [c.strip() for c in spec.split(",") if c.strip()]
    for chunk in chunks:
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            start = int(a.strip())
            end = int(b.strip())
            if start <= 0 or end <= 0:
                raise ValueError("Page numbers must be >= 1")
            if end < start:
                start, end = end, start
            out.update(range(start, end + 1))
        else:
            p = int(chunk)
            if p <= 0:
                raise ValueError("Page numbers must be >= 1")
            out.add(p)
    if not out:
        raise ValueError("Please enter at least one page")
    return out


def extract_catalog_price_dataframe_siemens_fallback(
    result,
    catalog_regex: str = SIEMENS_FULL_CATALOG_REGEX,
) -> pd.DataFrame:
    token_catalog_re = re.compile(
        r"(?i)\b(?:"
        r"[A-Z0-9]{2,}(?:[-/][A-Z0-9.]{1,})+"
        r"|"
        r"\d[A-Z]{2,}[A-Z0-9]{4,}"
        r")\b"
    )
    token_price_re = re.compile(r"(?<!\d)(\d[\d,]*)\s*\.-")
    records: list[dict] = []

    def _is_complete(token: str) -> bool:
        if is_complete_siemens_catalog is not None:
            return bool(is_complete_siemens_catalog(token, catalog_regex=catalog_regex))
        normalized = re.sub(r"[^A-Z0-9_./-]", "", token.upper())
        if not normalized or not normalized[0].isdigit():
            return False
        if "-" in normalized:
            first_segment = normalized.split("-", 1)[0]
            return (
                len(first_segment) >= 6
                and any(ch.isalpha() for ch in first_segment)
                and any(ch.isdigit() for ch in first_segment)
            )
        return bool(re.match(r"(?i)^\d[A-Z]{2,}[A-Z0-9]{4,}$", normalized)) and 7 <= len(
            normalized
        ) <= 24

    for t in result.tables:
        for ri, row in enumerate(t.rows):
            tokens: list[tuple[str, str, int]] = []
            for ci, cell in enumerate(row):
                text = str(cell or "").strip()
                if not text:
                    continue
                for m in token_catalog_re.finditer(text):
                    catalog = re.sub(r"[^A-Z0-9_./-]", "", m.group(0).strip().upper())
                    if _is_complete(catalog):
                        tokens.append(("catalog", catalog, ci))
                for m in token_price_re.finditer(text):
                    price = m.group(1).replace(",", "")
                    if price.isdigit() and len(price) >= 3:
                        tokens.append(("price", price, ci))

            for idx, (kind, value, col_idx) in enumerate(tokens):
                if kind != "catalog":
                    continue
                next_price = next((tup for tup in tokens[idx + 1 :] if tup[0] == "price"), None)
                if not next_price:
                    continue
                _, price_val, price_col = next_price
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
    if df.empty:
        return df
    return df.drop_duplicates(subset=["page", "catalog_no", "price"]).sort_values(
        by=["page", "table_id", "row_index", "catalog_col"]
    ).reset_index(drop=True)


def run_extraction(
    pdf_path: Path,
    selected_client: str,
    pages_spec: str,
    *,
    fast_mode: bool = False,
    text_only_mode: bool = False,
) -> tuple[pd.DataFrame, dict]:
    started = time.perf_counter()
    target_pages = parse_pages_spec(pages_spec)
    cfg = ExtractionConfig(
        run_word_qa=not fast_mode,
        use_camelot_fallback=False,
    )
    catalog_regex = (
        SIEMENS_FULL_CATALOG_REGEX
        if selected_client == SIEMENS_CLIENT
        else r"(?i)^[A-Z0-9_]{5,}$"
    )
    relaxed_retry_used = False
    auto_text_fallback_used = False
    struct_seconds = 0.0
    text_seconds = 0.0

    if text_only_mode:
        df_struct = pd.DataFrame()
    else:
        struct_started = time.perf_counter()
        if selected_client == L_AND_T_CLIENT:
            result = extract_keyword_tables(
                pdf_path, cfg, target_pages=target_pages, apply_keyword_filter=False
            )
            df_lt = extract_catalog_price_dataframe_l_and_t(result, catalog_regex=catalog_regex)
            df_std = extract_catalog_price_dataframe(result, catalog_regex=catalog_regex)
            df_struct = merge_catalog_price_results(df_lt, df_std)
        elif selected_client == ABB_CLIENT:
            result = extract_keyword_tables(
                pdf_path, cfg, target_pages=target_pages, apply_keyword_filter=False
            )
            df_struct = extract_catalog_price_dataframe(result, catalog_regex=catalog_regex)
        elif selected_client == SCHNEIDER_CLIENT:
            result = extract_keyword_tables(
                pdf_path, cfg, target_pages=target_pages, apply_keyword_filter=False
            )
            df_struct = extract_catalog_price_dataframe(result, catalog_regex=catalog_regex)
        else:
            result = extract_keyword_tables(
                pdf_path, cfg, target_pages=target_pages, apply_keyword_filter=False
            )
            if extract_catalog_price_dataframe_siemens is not None:
                df_struct = extract_catalog_price_dataframe_siemens(result, catalog_regex=catalog_regex)
            else:
                df_struct = extract_catalog_price_dataframe_siemens_fallback(
                    result, catalog_regex=catalog_regex
                )
        struct_seconds = time.perf_counter() - struct_started

    if fast_mode:
        # Fast path: skip text fallback unless structured extraction returns no rows.
        df = df_struct
        if df.empty:
            text_started = time.perf_counter()
            df = extract_catalog_price_from_pdf_text(
                pdf_path,
                target_pages=target_pages,
                catalog_regex=(
                    catalog_regex if selected_client != SIEMENS_CLIENT else SIEMENS_FULL_CATALOG_REGEX
                ),
                allow_numeric_catalog=(selected_client == L_AND_T_CLIENT),
                strip_trailing_order_stock_markers=(selected_client == ABB_CLIENT),
            )
            text_seconds += time.perf_counter() - text_started
            auto_text_fallback_used = not df.empty
    else:
        text_started = time.perf_counter()
        df_text = extract_catalog_price_from_pdf_text(
            pdf_path,
            target_pages=target_pages,
            catalog_regex=(
                catalog_regex if selected_client != SIEMENS_CLIENT else SIEMENS_FULL_CATALOG_REGEX
            ),
            allow_numeric_catalog=(selected_client == L_AND_T_CLIENT),
            strip_trailing_order_stock_markers=(selected_client == ABB_CLIENT),
        )
        text_seconds += time.perf_counter() - text_started
        df = merge_catalog_price_results(df_struct, df_text)
    if selected_client == SIEMENS_CLIENT and not df.empty and "catalog_no" in df.columns:
        df = df[df["catalog_no"].astype(str).str.match(SIEMENS_FULL_CATALOG_REGEX, na=False)]
    df = format_catalog_price_output(df)
    metrics = {
        "selected_client": selected_client,
        "pages_requested": sorted(target_pages),
        "relaxed_retry_used": relaxed_retry_used,
        "auto_text_fallback_used": auto_text_fallback_used,
        "rows": int(len(df)),
        "unique_catalogs": int(df["catalog_no"].nunique()) if "catalog_no" in df.columns else 0,
        "pages_returned": int(df["page"].nunique()) if "page" in df.columns else 0,
        "fast_mode": fast_mode,
        "text_only_mode": text_only_mode,
        "timing_seconds": {
            "struct": round(struct_seconds, 4),
            "text": round(text_seconds, 4),
            "total": round(time.perf_counter() - started, 4),
        },
    }
    return df, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract catalog-price rows from PDF.")
    parser.add_argument("--input", required=True, help="Input PDF path")
    parser.add_argument("--pages", required=True, help="Page spec, e.g. 22-30 or 14,16,20-25")
    parser.add_argument("--client", required=True, choices=sorted(SUPPORTED_CLIENTS))
    parser.add_argument("--output", required=True, help="Output CSV file path")
    parser.add_argument("--output-xlsx", default="", help="Optional output XLSX file path")
    parser.add_argument("--fast", action="store_true", help="Enable faster extraction path")
    parser.add_argument("--text-only", action="store_true", help="Use text-only extraction (fastest, may miss rows)")
    args = parser.parse_args()

    pdf_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    output_xlsx_path = Path(args.output_xlsx).resolve() if args.output_xlsx else None

    try:
        df, metrics = run_extraction(
            pdf_path,
            args.client,
            args.pages,
            fast_mode=args.fast,
            text_only_mode=args.text_only,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        xlsx_written = False
        xlsx_error = ""
        if output_xlsx_path is not None:
            output_xlsx_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                df.to_excel(output_xlsx_path, index=False)
                xlsx_written = True
            except ModuleNotFoundError as exc:
                xlsx_error = str(exc)
        result = {
            "ok": True,
            "output_csv": str(output_path),
            "output_xlsx": str(output_xlsx_path) if xlsx_written and output_xlsx_path is not None else "",
            "xlsx_written": xlsx_written,
            "xlsx_error": xlsx_error,
        }
        result.update(metrics)
        print(json.dumps(result))
        return 0
    except Exception as exc:  # broad by design for CLI caller
        result = {"ok": False, "error": str(exc)}
        print(json.dumps(result))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
