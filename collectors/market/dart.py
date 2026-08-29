# DART OpenAPI 호출 클라이언트. 종목코드 매핑과 증자·감자 이력을 준다

from __future__ import annotations

import io
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

from common.broker.errors import PermanentError, TransientError
from common.env import require_env

logger = logging.getLogger(__name__)

BASE = "https://opendart.fss.or.kr/api"
TIMEOUT = 30.0

# 사업보고서. 증자·감자 이력이 여기 누적돼 있다
ANNUAL_REPORT = "11011"


def _fetch(path: str, **params: Any) -> bytes:
    query = urllib.parse.urlencode({"crtfc_key": require_env("DART_API_KEY"), **params})
    try:
        with urllib.request.urlopen(f"{BASE}/{path}?{query}", timeout=TIMEOUT) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            raise TransientError(f"DART 서버 오류 (HTTP {exc.code})") from exc
        raise PermanentError(f"DART 거부 (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TransientError(f"DART 호출 실패: {type(exc).__name__}") from exc


def corp_codes() -> dict[str, str]:
    """종목코드 -> DART 고유번호.

    DART 는 자기 고유번호(8자리)를 쓴다. 종목코드로는 조회가 안 된다.
    전체 목록이 zip 으로 오고 상장사는 약 4000건이다.
    """
    with zipfile.ZipFile(io.BytesIO(_fetch("corpCode.xml"))) as archive:
        root = ET.fromstring(archive.read(archive.namelist()[0]))

    mapping = {}
    for item in root.findall("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        if stock_code:
            mapping[stock_code] = item.findtext("corp_code")
    return mapping


def share_events(corp_code: str, year: int) -> list[dict[str, str]]:
    """그 해 사업보고서의 증자·감자 이력.

    `isu_dcrs_stle` 에 발행형태가 온다. 유상증자·무상증자·주식분할·
    전환권행사·신주인수권행사·주식매수선택권행사 등으로 구분된다.

    **`isu_dcrs_de` 는 발행일이라 상장주식수 변경일과 다르다.**
    삼양홀딩스는 DART 2025.11.06 인데 상장주식수는 2025-11-24 에 바뀌었다.
    날짜로 맞추면 안 되고 수량으로 맞춰야 한다.
    """
    data = json.loads(
        _fetch(
            "irdsSttus.json",
            corp_code=corp_code,
            bsns_year=str(year),
            reprt_code=ANNUAL_REPORT,
        )
    )
    status = data.get("status")
    if status == "013":  # 조회된 데이터가 없음
        return []
    if status != "000":
        raise PermanentError(f"DART 오류 {status}: {data.get('message')}")

    return [
        row
        for row in (data.get("list") or [])
        if row.get("isu_dcrs_de", "-") not in ("-", "", None)
    ]
