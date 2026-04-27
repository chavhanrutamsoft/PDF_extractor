"""Unit tests for cleaning and header/body split."""

from pdf_table_pipeline.clean import clean_rows, rows_to_headers_data
from pdf_table_pipeline.config import ExtractionConfig


def test_rows_to_headers_preserves_body_when_shallow():
    rows = [
        ["H1", "H2"],
        ["a", "b"],
    ]
    headers, data = rows_to_headers_data(rows, header_rows=2)
    assert len(data) >= 1
    assert data[0] == ["a", "b"]


def test_forward_fill_skips_numeric_seed():
    cfg = ExtractionConfig(
        forward_fill_column_indices=[0],
        never_forward_fill_numeric_columns=True,
    )
    rows = [
        ["CatA", "10"],
        [None, "20"],
    ]
    cleaned, _ = clean_rows(rows, cfg)
    assert cleaned[1][0] == "CatA"
