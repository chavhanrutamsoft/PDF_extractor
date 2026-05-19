#!/usr/bin/env python3
"""CLI: extract keyword-filtered tables from a PDF to JSON, CSV, or Parquet."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pdf_table_pipeline.config import ExtractionConfig
from pdf_table_pipeline.pipeline import (
    extract_catalog_price_dataframe,
    extract_catalog_price_from_pdf_text,
    extract_keyword_tables,
    format_catalog_price_output,
    merge_catalog_price_results,
    tables_to_long_dataframe,
    tables_to_wide_dataframe,
)


def parse_pages_spec(spec: str) -> set[int]:
    """
    Parse page spec like '5', '5,7,10-15' into a 1-based page set.
    """
    out: set[int] = set()
    chunks = [c.strip() for c in spec.split(",") if c.strip()]
    for chunk in chunks:
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            start = int(a.strip())
            end = int(b.strip())
            if start <= 0 or end <= 0:
                raise ValueError("page numbers must be >= 1")
            if end < start:
                start, end = end, start
            out.update(range(start, end + 1))
        else:
            p = int(chunk)
            if p <= 0:
                raise ValueError("page numbers must be >= 1")
            out.add(p)
    if not out:
        raise ValueError("empty page spec")
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Keyword-driven PDF table extraction")
    p.add_argument("--pdf", required=True, type=Path, help="Path to input PDF")
    p.add_argument("--config", type=Path, help="YAML config (optional)")
    p.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output file path (.json, .csv, or .parquet)",
    )
    p.add_argument(
        "--format",
        choices=("json", "csv", "parquet", "xlsx", "long_csv", "long_parquet", "long_xlsx"),
        default=None,
        help="Override output format inferred from --out suffix",
    )
    p.add_argument(
        "--wide",
        action="store_true",
        help="For tabular export: wide layout (one row per table row). Default without --long.",
    )
    p.add_argument(
        "--long",
        action="store_true",
        help="For tabular export: long layout (table_id, page, row_index, col_name, value)",
    )
    p.add_argument(
        "--pages",
        type=str,
        default=None,
        help='Only process these 1-based pages, e.g. "16" or "16,18,20-25"',
    )
    p.add_argument(
        "--catalog-only",
        action="store_true",
        help="Export only catalog_no and adjacent price pairs from extracted table rows.",
    )
    p.add_argument(
        "--catalog-regex",
        type=str,
        default=r"(?i)^[A-Z0-9_]{5,}$",
        help="Regex used to detect catalog number cells.",
    )
    p.add_argument(
        "--skip-keyword-filter",
        action="store_true",
        help="Do not filter tables by keywords (useful with --pages and --catalog-only).",
    )
    p.add_argument(
        "--allow-text-fallback",
        action="store_true",
        help="Allow text-line fallback when structured Reference->MRP extraction misses a page (less accurate).",
    )
    args = p.parse_args()

    fmt = args.format
    if fmt is None:
        suf = args.out.suffix.lower()
        if suf == ".json":
            fmt = "json"
        elif suf == ".csv":
            fmt = "csv"
        elif suf == ".parquet":
            fmt = "parquet"
        elif suf == ".xlsx":
            fmt = "xlsx"
        else:
            p.error("Cannot infer --format from output path; pass --format explicitly")

    cfg = ExtractionConfig.from_yaml(args.config) if args.config else ExtractionConfig()

    if not args.pdf.is_file():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    target_pages = parse_pages_spec(args.pages) if args.pages else None
    apply_keyword_filter = not args.skip_keyword_filter
    if args.catalog_only and target_pages:
        # Page-directed catalog extraction should not miss tables due header keyword mismatch.
        apply_keyword_filter = False
    result = extract_keyword_tables(
        args.pdf,
        cfg,
        target_pages=target_pages,
        apply_keyword_filter=apply_keyword_filter,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json" and not args.catalog_only:
        args.out.write_text(
            json.dumps(result.to_json_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Wrote {len(result.tables)} tables to {args.out}")
        return 0

    if args.catalog_only:
        df_struct = extract_catalog_price_dataframe(result, catalog_regex=args.catalog_regex)
        if args.allow_text_fallback:
            df_text = extract_catalog_price_from_pdf_text(
                args.pdf,
                target_pages=target_pages,
                catalog_regex=args.catalog_regex,
            )
            df = merge_catalog_price_results(df_struct, df_text)
        else:
            df = df_struct
        df = format_catalog_price_output(df)
    elif args.long or fmt.startswith("long_"):
        df = tables_to_long_dataframe(result)
    else:
        df = tables_to_wide_dataframe(result)

    if fmt in ("csv", "long_csv"):
        df.to_csv(args.out, index=False, encoding="utf-8")
    elif fmt in ("parquet", "long_parquet"):
        try:
            df.to_parquet(args.out, index=False)
        except ImportError:
            print(
                "Parquet requires pyarrow or fastparquet: pip install pyarrow",
                file=sys.stderr,
            )
            return 3
    elif fmt in ("xlsx", "long_xlsx"):
        try:
            df.to_excel(args.out, index=False)
        except ImportError:
            print("Excel export requires openpyxl: pip install openpyxl", file=sys.stderr)
            return 4
    else:
        p.error(f"Unsupported tabular format: {fmt}")

    print(f"Wrote {len(df)} rows ({len(result.tables)} tables) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
