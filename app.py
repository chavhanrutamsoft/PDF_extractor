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


def hide_streamlit_top_controls() -> None:
    """Hide extra Streamlit chrome without removing the sidebar toggle."""
    st.markdown(
        """
        <style>
            [data-testid="stDecoration"],
            [data-testid="stStatusWidget"] {
                display: none !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="PDF Catalog Price Extractor", layout="wide")
    hide_streamlit_top_controls()
    st.title("PDF Catalog Price Extractor")
    st.caption(
        "Upload PDF, enter pages (e.g. 16,18,20-25), and download extracted catalog-price data."
    )
    st.markdown(
        "Use the fields below to extract catalog numbers and prices from selected PDF pages."
    )

    with st.container():
        st.subheader("Extraction Settings")
        col1, col2 = st.columns([1.3, 1])
        with col1:
            pages_text = st.text_input(
                "Pages",
                value="",
                placeholder="e.g. 14 or 16,18,20-25",
            )
            st.caption("Enter single pages, comma-separated pages, or ranges.")
            catalog_regex = st.text_input(
                "Catalog Regex",
                value=r"(?i)^[A-Z0-9_]{5,}$",
                help="Use a stricter pattern only if needed, e.g. ^[C][A-Z0-9]{6,}$",
            )
        with col2:
            skip_keyword_filter = st.checkbox(
                "Skip keyword filter for selected pages",
                value=True,
            )
            allow_text_fallback = st.checkbox(
                "Allow text fallback",
                value=False,
                help="Less accurate, but can recover rows when strict structured parsing misses them.",
            )
            st.caption("Recommended: keep keyword filter skipped for page-specific extraction.")

    st.divider()

    upload_col, action_col = st.columns([1.6, 0.6])
    with upload_col:
        uploaded_pdf = st.file_uploader("Upload PDF", type=["pdf"])
    with action_col:
        st.write("")
        st.write("")
        run_btn = st.button("Process PDF", type="primary", disabled=uploaded_pdf is None, use_container_width=True)

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
        applied_keyword_filter = not skip_keyword_filter
        result = extract_keyword_tables(
            temp_pdf_path,
            cfg,
            target_pages=target_pages,
            apply_keyword_filter=applied_keyword_filter,
        )

        df_struct = extract_catalog_price_dataframe(result, catalog_regex=catalog_regex)
        relaxed_retry_used = False
        if df_struct.empty and applied_keyword_filter:
            # Auto-retry for pages where headers do not contain configured keywords
            # but Reference/LP data is present (e.g., product spotlight pages).
            result = extract_keyword_tables(
                temp_pdf_path,
                cfg,
                target_pages=target_pages,
                apply_keyword_filter=False,
            )
            df_struct = extract_catalog_price_dataframe(result, catalog_regex=catalog_regex)
            relaxed_retry_used = not df_struct.empty
        auto_text_fallback_used = False
        if allow_text_fallback:
            df_text = extract_catalog_price_from_pdf_text(
                temp_pdf_path,
                target_pages=target_pages,
                catalog_regex=catalog_regex,
            )
            df = merge_catalog_price_results(df_struct, df_text)
        else:
            df = df_struct
            if df.empty:
                # Graceful recovery for pages where strict structured parsing misses rows.
                df_text = extract_catalog_price_from_pdf_text(
                    temp_pdf_path,
                    target_pages=target_pages,
                    catalog_regex=catalog_regex,
                )
                df = merge_catalog_price_results(df_struct, df_text)
                auto_text_fallback_used = not df.empty
        df = format_catalog_price_output(df)

    if df.empty:
        st.warning("No catalog-price rows found for the selected pages.")
        return

    if relaxed_retry_used:
        st.info("No rows in strict keyword mode; auto-retried with relaxed page filter.")
    if auto_text_fallback_used:
        st.info("No rows in strict structured mode; auto-retried with text fallback.")

    metric_col1, metric_col2 = st.columns([0.35, 0.65])
    with metric_col1:
        st.metric("Rows Found", len(df))
    with metric_col2:
        st.success(f"Extraction complete for {uploaded_pdf.name}.")

    st.dataframe(df, use_container_width=True, hide_index=True)

    out_stem = f"{Path(uploaded_pdf.name).stem}_catalog_prices"
    try:
        excel_bytes = dataframe_to_excel_bytes(df)
        dl_col1, dl_col2 = st.columns([0.3, 0.7])
        with dl_col1:
            st.download_button(
                "Download Excel",
                data=excel_bytes,
                file_name=f"{out_stem}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
    except ModuleNotFoundError:
        # Fallback for environments where openpyxl is unavailable.
        csv_bytes = df.to_csv(index=False).encode("utf-8")
        st.warning("openpyxl is not installed in this environment. Downloading CSV instead.")
        dl_col1, dl_col2 = st.columns([0.3, 0.7])
        with dl_col1:
            st.download_button(
                "Download CSV",
                data=csv_bytes,
                file_name=f"{out_stem}.csv",
                mime="text/csv",
                use_container_width=True,
            )


if __name__ == "__main__":
    main()

