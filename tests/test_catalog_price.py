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


def test_merged_lp_cell_one_price_many_ordering_codes():
    """Vertically merged price column: same LP applies to many ordering-code rows."""
    table = ExtractedTable(
        table_id="p001_t01",
        page=1,
        page_index_0=0,
        headers=["Ordering code", "L.P.(Rs)"],
        rows=[
            ["1SDA066799R1", ""],
            ["1SDA066800R1", ""],
            ["1SDA066801R1", "12,450"],
            ["1SDA066808R1", "18,850"],
        ],
    )
    result = ExtractionResult(source_pdf="x.pdf", tables=[table])
    df = extract_catalog_price_dataframe(result)
    assert len(df) == 4
    by_cat = dict(zip(df["catalog_no"], df["price"], strict=True))
    assert by_cat["1SDA066799R1"] == "12450"
    assert by_cat["1SDA066800R1"] == "12450"
    assert by_cat["1SDA066801R1"] == "12450"
    assert by_cat["1SDA066808R1"] == "18850"


def test_abb_header_catalogs_with_merged_lp_price():
    """pdfplumber often places first ordering codes and LP in merged header cells."""
    table = ExtractedTable(
        table_id="p036_t04",
        page=36,
        page_index_0=35,
        headers=["Ordering code | 1SDA067044R1 | 1SDA067045R1", "L.P. (`) | 24,000"],
        rows=[
            ["1SDA067046R1", ""],
            ["1SDA067047R1", ""],
            ["1SDA067048R1 n", ""],
            ["1SDA067049R1 n", "23,670"],
            ["1SDA067050R1 n", ""],
        ],
    )
    result = ExtractionResult(source_pdf="x.pdf", tables=[table])
    df = extract_catalog_price_dataframe(result)
    by_cat = dict(zip(df["catalog_no"], df["price"], strict=True))
    assert by_cat["1SDA067044R1"] == "24000"
    assert by_cat["1SDA067045R1"] == "24000"
    assert by_cat["1SDA067046R1"] == "24000"
    assert by_cat["1SDA067047R1"] == "24000"
    assert by_cat["1SDA067048R1"] == "24000"
    assert by_cat["1SDA067049R1"] == "23670"
    assert by_cat["1SDA067050R1"] == "23670"


def test_merged_lp_multiple_price_groups_in_one_column():
    """Several LP groups in one column: header LP, then mid-table LP, then next LP."""
    table = ExtractedTable(
        table_id="p036_t04",
        page=36,
        page_index_0=35,
        headers=["Ordering code | 1SDA067044R1", "L.P. (`) | 24,000"],
        rows=[
            ["1SDA067046R1", ""],
            ["1SDA067047R1", ""],
            ["1SDA067048R1", ""],
            ["1SDA067049R1", "23,670"],
            ["1SDA067050R1", ""],
            ["1SDA076529R1", "34,570"],
        ],
    )
    result = ExtractionResult(source_pdf="x.pdf", tables=[table])
    df = extract_catalog_price_dataframe(result)
    by_cat = dict(zip(df["catalog_no"], df["price"], strict=True))
    assert by_cat["1SDA067046R1"] == "24000"
    assert by_cat["1SDA067050R1"] == "23670"
    assert by_cat["1SDA076529R1"] == "34570"

