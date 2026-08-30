# DART 공시 적재. 목록 응답을 적재 형식으로 바꾸는 규칙을 고정한다

from datetime import UTC, datetime

from collectors.news.dart import to_disclosure

KNOWN = {"KRX:005930"}

ROW = {
    "rcept_no": "20260828000123",
    "rcept_dt": "20260828",
    "corp_name": "삼성전자",
    "stock_code": "005930",
    "report_nm": "  주요사항보고서\n(자기주식취득결정)  ",
}


def test_maps_stock_id_with_exchange_prefix():
    record = to_disclosure(ROW, "B", KNOWN)
    assert record.stock_id == "KRX:005930"
    assert record.disclosure_type == "B"
    assert record.url.endswith("20260828000123")


def test_report_name_is_squeezed():
    assert (
        to_disclosure(ROW, "B", KNOWN).report_name
        == "주요사항보고서 (자기주식취득결정)"
    )


def test_unknown_stock_is_stored_without_link():
    """우리 종목 표에 없으면 비운다. 외래키가 걸려 있어 넣으면 실패한다."""
    record = to_disclosure(dict(ROW, stock_code="999999"), "B", KNOWN)
    assert record.stock_id is None
    assert record.corp_name == "삼성전자"


def test_date_only_becomes_kst_midnight():
    """목록 API 는 시각을 주지 않는다. 그날 0시(KST)로 둔다."""
    record = to_disclosure(ROW, "B", KNOWN)
    assert record.submitted_at == datetime(2026, 8, 27, 15, 0, tzinfo=UTC)


def test_rows_without_receipt_number_are_dropped():
    assert to_disclosure(dict(ROW, rcept_no=""), "B", KNOWN) is None
    assert to_disclosure(dict(ROW, rcept_dt="2026"), "B", KNOWN) is None
