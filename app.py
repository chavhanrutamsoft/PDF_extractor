#!/usr/bin/env python3
"""Streamlit UI for page-targeted catalog/price extraction from PDF."""

from __future__ import annotations

import io
import re
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
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
)


def parse_pages_spec(spec: str) -> set[int]:
    """Parse '5' or '5,7,10-15' into 1-based page set."""
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


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Write DataFrame to in-memory Excel for download."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="extracted_data")
    buf.seek(0)
    return buf.getvalue()


def main() -> None:
    st.set_page_config(page_title="PDF Catalog Price Extractor", layout="wide")
    st.title("PDF Catalog Price Extractor")
    st.caption(
        "Upload PDF, enter pages (e.g. 16,18,20-25), and download extracted catalog-price data."
    )

    with st.sidebar:
        st.subheader("Extraction Settings")
        pages_text = st.text_input("Pages (comma/range)", value="16-20")
        catalog_regex = st.text_input(
            "Catalog regex",
            value=r"(?i)^[A-Z0-9_]{5,}$",
            help="Use strict pattern if needed, e.g. ^[C][A-Z0-9]{6,}$",
        )
        skip_keyword_filter = st.checkbox(
            "Skip keyword filter for selected pages",
            value=True,
        )

    uploaded_pdf = st.file_uploader("Upload PDF", type=["pdf"])
    run_btn = st.button("Process PDF", type="primary", disabled=uploaded_pdf is None)

    if not run_btn:
        return

    if uploaded_pdf is None:
        st.warning("Please upload a PDF first.")
        return

    try:
        target_pages = parse_pages_spec(pages_text)
    except ValueError as exc:
        st.error(f"Invalid page input: {exc}")
        return

    try:
        re.compile(catalog_regex)
    except re.error as exc:
        st.error(f"Invalid catalog regex: {exc}")
        return

    with st.spinner("Processing PDF..."):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uploaded_pdf.getvalue())
            temp_pdf_path = Path(tmp.name)

        cfg = ExtractionConfig()
        result = extract_keyword_tables(
            temp_pdf_path,
            cfg,
            target_pages=target_pages,
            apply_keyword_filter=not skip_keyword_filter,
        )

        df_struct = extract_catalog_price_dataframe(result, catalog_regex=catalog_regex)
        df_text = extract_catalog_price_from_pdf_text(
            temp_pdf_path,
            target_pages=target_pages,
            catalog_regex=catalog_regex,
        )
        if not df_struct.empty and "page" in df_struct.columns:
            struct_pages = set(df_struct["page"].astype(int).tolist())
            if "page" in df_text.columns:
                df_text = df_text[~df_text["page"].astype(int).isin(struct_pages)]
        df = merge_catalog_price_results(df_struct, df_text)
        df = format_catalog_price_output(df)

    if df.empty:
        st.warning("No catalog-price rows found for the selected pages.")
        return

    st.success(f"Extraction complete. Found {len(df)} rows.")
    st.dataframe(df, use_container_width=True, hide_index=True)

    excel_bytes = dataframe_to_excel_bytes(df)
    out_name = f"{Path(uploaded_pdf.name).stem}_catalog_prices.xlsx"
    st.download_button(
        "Download Excel",
        data=excel_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    main()

