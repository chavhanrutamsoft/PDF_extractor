from pdf_table_pipeline.models import ExtractedTable, ExtractionResult
from pdf_table_pipeline.pipeline import extract_catalog_price_dataframe


def test_extract_catalog_price_from_four_column_pattern():
    table = ExtractedTable(
        table_id="p001_t01",
        page=1,
        page_index_0=0,
        headers=[
            "Three Pole Reference",
            "Unit MRP [₹]",
            "Four Pole Reference",
            "Unit MRP [₹]",
        ],
        rows=[
            ["C10B3TM016", "17860", "C10B6TM016", "25630"],
            ["C10B3TM025", "17860", "C10B6TM025", "25630"],
        ],
    )
    result = ExtractionResult(source_pdf="x.pdf", tables=[table])
    df = extract_catalog_price_dataframe(result)
    assert len(df) == 4
    assert set(df["catalog_no"].tolist()) == {
        "C10B3TM016",
        "C10B6TM016",
        "C10B3TM025",
        "C10B6TM025",
    }
    assert "17860" in set(df["price"].tolist())
    assert "25630" in set(df["price"].tolist())

