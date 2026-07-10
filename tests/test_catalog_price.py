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


def test_multi_column_catalog_price_pairs_without_labels():
    """Any page: multiple ref+mrp column pairs inferred from cell content."""
    table = ExtractedTable(
        table_id="p010_t01",
        page=10,
        page_index_0=9,
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
    assert set(df["price"].tolist()) == {"17860", "25630"}


def test_abb_pse_table_without_column_labels():
    """Data-only headers: infer order-code + LP columns; skip Type (PSE/PSTX)."""
    table = ExtractedTable(
        table_id="p138_t01",
        page=138,
        page_index_0=137,
        headers=[
            "7.5 | 11 | 15",
            "18 | 25 | 30",
            "PSE18-600-70 | PSE25-600-70 | PSE30-600-70",
            "1SFA897101R7000 | 1SFA897102R7000 | 1SFA897103R7000",
            "99,190 | 1,00,650 | 1,22,930",
        ],
        rows=[
            ["18.5", "37", "PSE37-600-70", "1SFA897104R7000", "1,48,420"],
            ["22", "45", "PSE45-600-70", "1SFA897105R7000", "1,68,470"],
        ],
    )
    result = ExtractionResult(source_pdf="x.pdf", tables=[table])
    df = extract_catalog_price_dataframe(result)
    assert "PSE37" not in "".join(df["catalog_no"].tolist())
    assert "PSE45" not in "".join(df["catalog_no"].tolist())
    by_cat = dict(zip(df["catalog_no"], df["price"], strict=True))
    assert by_cat["1SFA897101R7000"] == "99190"
    assert by_cat["1SFA897104R7000"] == "148420"
    assert by_cat["1SFA897105R7000"] == "168470"


def test_abb_upon_request_merged_lp_column():
    table = ExtractedTable(
        table_id="p138_t02",
        page=138,
        page_index_0=137,
        headers=[
            "15 | 18.5 | 22",
            "30 | 37 | 45",
            "PSTX30-600-70 | PSTX37-600-70 | PSTX45-600-70",
            "1SFA898103R7000 | 1SFA898104R7000 | 1SFA898105R7000",
            "Upon request",
        ],
        rows=[
            ["30", "60", "PSTX60-600-70", "1SFA898106R7000", None],
            ["37", "72", "PSTX72-600-70", "1SFA898107R7000", None],
        ],
    )
    result = ExtractionResult(source_pdf="x.pdf", tables=[table])
    df = extract_catalog_price_dataframe(result)
    assert all(p == "On Request" for p in df["price"].tolist())
    assert "PSTX60" not in "".join(df["catalog_no"].tolist())
    assert "1SFA898106R7000" in df["catalog_no"].tolist()


def test_text_fallback_rejects_type_column_tokens():
    """Line text must not treat PSE/PSTX type labels as catalog numbers."""
    from pdf_table_pipeline.pipeline import is_probable_order_code

    assert not is_probable_order_code("PSE18", raw="PSE18")
    assert not is_probable_order_code("PSE105", raw="PSE105")
    assert not is_probable_order_code("PSTX1050", raw="PSTX1050")
    assert is_probable_order_code("1SFA897101R7000", raw="1SFA897101R7000")


def test_siemens_accepts_hyphenated_and_compact_catalogs():
    """MCCB hyphenated codes and Betagard compact Reference Nos both valid."""
    from pdf_table_pipeline.models import ExtractedTable, ExtractionResult
    from pdf_table_pipeline.pipeline import (
        SIEMENS_FULL_CATALOG_REGEX,
        extract_catalog_price_dataframe_siemens,
        is_complete_siemens_catalog,
    )
    import re

    cat_re = re.compile(SIEMENS_FULL_CATALOG_REGEX)
    assert is_complete_siemens_catalog("3WJ1108-2AF02-1AA0")
    assert is_complete_siemens_catalog("5SL61057RC")
    assert is_complete_siemens_catalog("8GB9901")
    assert is_complete_siemens_catalog("8GB9905LSP")
    assert not is_complete_siemens_catalog("2AF02-1AA0")  # fragment
    assert not is_complete_siemens_catalog("5SL6")  # too short
    assert cat_re.match("5SL61057RC")
    assert cat_re.match("3WJ1108-2AF02-1AA0")
    assert not cat_re.match("2AF02-1AA0")

    table = ExtractedTable(
        table_id="p005_t01",
        page=5,
        page_index_0=4,
        headers=["Reference No", "Unit MRP"],
        rows=[
            ["5SL61057RC", "697.-"],
            ["8GB9901", "175.-"],
            ["3WJ1108-2AF02-1AA0", "334220.-"],
        ],
    )
    result = ExtractionResult(source_pdf="x.pdf", tables=[table])
    df = extract_catalog_price_dataframe_siemens(result)
    by_cat = dict(zip(df["catalog_no"], df["price"], strict=True))
    assert by_cat["5SL61057RC"] == "697"
    assert by_cat["8GB9901"] == "175"
    assert by_cat["3WJ1108-2AF02-1AA0"] == "334220"

