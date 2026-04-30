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
from pdf_table_pipeline import pipeline as pipeline_mod

extract_catalog_price_dataframe = pipeline_mod.extract_catalog_price_dataframe
extract_catalog_price_dataframe_siemens = getattr(pipeline_mod, "extract_catalog_price_dataframe_siemens", None)
extract_catalog_price_from_pdf_text = pipeline_mod.extract_catalog_price_from_pdf_text
extract_keyword_tables = pipeline_mod.extract_keyword_tables
format_catalog_price_output = pipeline_mod.format_catalog_price_output
merge_catalog_price_results = pipeline_mod.merge_catalog_price_results

SCHNEIDER_CLIENT = "Schneider"
SIEMENS_CLIENT = "Siemens"
# Accept full Siemens type codes such as:
# - 3WJ1108-2AF02-1AA0 (multi-hyphen)
# - 3WJ9111-0AD01 (single-hyphen)
SIEMENS_FULL_CATALOG_REGEX = r"(?i)^\d[A-Z0-9]{5,}(?:-[A-Z0-9.]{4,})+$"


def extract_catalog_price_dataframe_siemens_fallback(
    result,
    catalog_regex: str = SIEMENS_FULL_CATALOG_REGEX,
) -> pd.DataFrame:
    """Cloud-safe Siemens parser when pipeline module is outdated."""
    cat_re = re.compile(catalog_regex)
    token_catalog_re = re.compile(r"(?i)\b[A-Z0-9]{2,}(?:[-/][A-Z0-9.]{2,})+\b")
    token_price_re = re.compile(r"(?<!\d)(\d[\d,]*)\s*\.-")
    records: list[dict] = []

    def is_complete_siemens_catalog(token: str) -> bool:
        normalized = re.sub(r"[^A-Z0-9_./-]", "", token.upper())
        if not normalized or not cat_re.match(normalized):
            return False
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
            tokens: list[tuple[str, str, int]] = []
            for ci, cell in enumerate(row):
                text = str(cell or "").strip()
                if not text:
                    continue
                for m in token_catalog_re.finditer(text):
                    catalog = re.sub(r"[^A-Z0-9_./-]", "", m.group(0).strip().upper())
                    if is_complete_siemens_catalog(catalog):
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
    """Hide Streamlit top chrome/buttons for a clean app shell."""
    st.markdown(
        """
        <style>
            .stApp {
                background: radial-gradient(circle at top right, #eef4ff 0%, #f8fbff 35%, #ffffff 75%);
            }
            .block-container {
                padding-top: 1.25rem !important;
                padding-bottom: 2.2rem !important;
            }
            .app-card {
                border: 1px solid #dbe5ff;
                border-radius: 14px;
                padding: 14px 16px;
                background: #ffffffcc;
                box-shadow: 0 4px 12px rgba(18, 50, 120, 0.06);
                margin-bottom: 10px;
            }
            .app-muted {
                color: #4b5f7f;
                font-size: 0.93rem;
            }
            .kpi-wrap {
                border: 1px solid #d9e4ff;
                border-radius: 10px;
                padding: 10px 12px;
                background: #f8fbff;
            }
            .stDownloadButton button {
                border-radius: 10px;
                font-weight: 600;
            }
            .stButton button {
                border-radius: 10px;
                font-weight: 600;
            }
            /* Remove top white header line while keeping page controls functional */
            [data-testid="stHeader"] {
                background: transparent !important;
                border-bottom: none !important;
                box-shadow: none !important;
            }
            [data-testid="stDecoration"],
            [data-testid="stStatusWidget"] {
                display: none !important;
            }
            /* Hide top-right Streamlit action buttons (Share/GitHub/etc.) */
            [data-testid="stToolbarActions"] {
                display: none !important;
            }
            [data-testid="stToolbar"] a,
            [data-testid="stToolbar"] button[kind="header"] {
                display: none !important;
            }
            /* Hide close button so user cannot collapse sidebar */
            [data-testid="stSidebarCollapseButton"],
            button[title="Close sidebar"] {
                display: none !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(
        page_title="PDF Catalog Price Extractor",
        page_icon="📄",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    hide_streamlit_top_controls()
    st.markdown(
        """
        <div class="app-card">
            <h2 style="margin:0 0 0.35rem 0;">PDF Catalog Price Extractor</h2>
            <div class="app-muted">
                Upload your PDF, choose target pages (e.g. <b>14</b> or <b>16,18,20-25</b>),
                and export clean catalog-price results.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.subheader("Extraction Settings")
        st.caption("Tune matching quality and recovery behavior.")
        selected_client = st.selectbox(
            "PDF Client",
            options=[SCHNEIDER_CLIENT, SIEMENS_CLIENT],
            index=0,
            help="Choose the vendor format before processing the PDF.",
        )
        default_catalog_regex = (
            r"(?i)^[A-Z0-9_]{5,}$"
            if selected_client == SCHNEIDER_CLIENT
            else SIEMENS_FULL_CATALOG_REGEX
        )
        pages_text = st.text_input("Pages (comma/range)", value="")
        catalog_regex = st.text_input(
            "Catalog regex",
            value=default_catalog_regex,
            help="Use strict pattern if needed, e.g. ^[C][A-Z0-9]{6,}$",
        )
        skip_keyword_filter = st.checkbox(
            "Skip keyword filter for selected pages",
            value=True,
        )
        allow_text_fallback = st.checkbox(
            "Allow text fallback (less accurate)",
            value=True,
            help="OFF = strict Reference->MRP only (recommended). ON can recover missed pages but may pick wrong column tokens.",
        )
        st.markdown("---")
        st.markdown(
            """
            <div class="app-muted">
                <b>Tip:</b> If a page returns no rows, keep <i>Skip keyword filter</i> ON.
            </div>
            """,
            unsafe_allow_html=True,
        )
        if selected_client == SIEMENS_CLIENT:
            st.info("Siemens mode enabled: parser uses Siemens-friendly catalog matching.")

    top_left, top_right = st.columns([1.45, 1.0], gap="large")
    with top_left:
        uploaded_pdf = st.file_uploader("Upload PDF", type=["pdf"])
    with top_right:
        st.markdown('<div class="kpi-wrap">', unsafe_allow_html=True)
        st.metric("Target Pages", pages_text if pages_text.strip() else "Not set")
        st.caption("Enter at least one page number to run extraction.")
        st.markdown("</div>", unsafe_allow_html=True)

    run_btn = st.button(
        "Process PDF",
        type="primary",
        disabled=uploaded_pdf is None or not pages_text.strip(),
        use_container_width=True,
    )

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

    with st.spinner(f"Processing {selected_client} PDF..."):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uploaded_pdf.getvalue())
            temp_pdf_path = Path(tmp.name)

        cfg = ExtractionConfig()
        relaxed_retry_used = False
        auto_text_fallback_used = False

        if selected_client == SCHNEIDER_CLIENT:
            # Keep existing Schneider behavior unchanged.
            applied_keyword_filter = not skip_keyword_filter
            result = extract_keyword_tables(
                temp_pdf_path,
                cfg,
                target_pages=target_pages,
                apply_keyword_filter=applied_keyword_filter,
            )
            df_struct = extract_catalog_price_dataframe(result, catalog_regex=catalog_regex)
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
        else:
            # Siemens mode: default to relaxed keyword filtering because keyword set
            # is currently Schneider-oriented.
            applied_keyword_filter = False if skip_keyword_filter else True
            result = extract_keyword_tables(
                temp_pdf_path,
                cfg,
                target_pages=target_pages,
                apply_keyword_filter=applied_keyword_filter,
            )
            if extract_catalog_price_dataframe_siemens is not None:
                df_struct = extract_catalog_price_dataframe_siemens(
                    result, catalog_regex=catalog_regex
                )
            else:
                df_struct = extract_catalog_price_dataframe_siemens_fallback(
                    result, catalog_regex=catalog_regex
                )
            if df_struct.empty and applied_keyword_filter:
                df_text = extract_catalog_price_from_pdf_text(
                    temp_pdf_path,
                    target_pages=target_pages,
                    catalog_regex=catalog_regex,
                )
                df_struct = merge_catalog_price_results(df_struct, df_text)
                relaxed_retry_used = not df_struct.empty

        if allow_text_fallback:
            text_fallback_catalog_regex = (
                catalog_regex
                if selected_client == SCHNEIDER_CLIENT
                else SIEMENS_FULL_CATALOG_REGEX
            )
            df_text = extract_catalog_price_from_pdf_text(
                temp_pdf_path,
                target_pages=target_pages,
                catalog_regex=text_fallback_catalog_regex,
            )
            df = merge_catalog_price_results(df_struct, df_text)
        else:
            df = df_struct
            if df.empty:
                # Graceful recovery for pages where strict structured parsing misses rows.
                df_text = extract_catalog_price_from_pdf_text(
                    temp_pdf_path,
                    target_pages=target_pages,
                    catalog_regex=(
                        catalog_regex
                        if selected_client == SCHNEIDER_CLIENT
                        else SIEMENS_FULL_CATALOG_REGEX
                    ),
                )
                df = merge_catalog_price_results(df_struct, df_text)
                auto_text_fallback_used = not df.empty
        if selected_client == SIEMENS_CLIENT and not df.empty and "catalog_no" in df.columns:
            df = df[df["catalog_no"].astype(str).str.match(SIEMENS_FULL_CATALOG_REGEX, na=False)]
        df = format_catalog_price_output(df)

    if df.empty:
        st.warning("No catalog-price rows found for the selected pages.")
        return

    if relaxed_retry_used:
        st.info("No rows in strict keyword mode; auto-retried with relaxed page filter.")
    if auto_text_fallback_used:
        st.info("No rows in strict structured mode; auto-retried with text fallback.")

    st.success(f"Extraction complete. Found {len(df)} rows.")
    k1, k2, k3 = st.columns(3)
    with k1:
        st.metric("Rows", len(df))
    with k2:
        st.metric("Unique Catalogs", df["catalog_no"].nunique() if "catalog_no" in df.columns else 0)
    with k3:
        st.metric("Pages Returned", df["page"].nunique() if "page" in df.columns else 0)

    tab_data, tab_preview = st.tabs(["Extracted Data", "Quick Preview"])
    with tab_data:
        st.dataframe(df, use_container_width=True, hide_index=True)
    with tab_preview:
        st.markdown("Top 10 rows")
        st.table(df.head(10))

    out_stem = f"{Path(uploaded_pdf.name).stem}_{selected_client.lower()}_catalog_prices"
    dl1, dl2 = st.columns(2)
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    with dl1:
        st.download_button(
            "Download CSV",
            data=csv_bytes,
            file_name=f"{out_stem}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with dl2:
        try:
            excel_bytes = dataframe_to_excel_bytes(df)
            st.download_button(
                "Download Excel",
                data=excel_bytes,
                file_name=f"{out_stem}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        except ModuleNotFoundError:
            st.info("openpyxl not installed in this environment. Use CSV download.")


if __name__ == "__main__":
    main()

