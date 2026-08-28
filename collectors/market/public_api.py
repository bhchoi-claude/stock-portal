# data.go.kr 과 ECOS 호출 클라이언트. 지표 수집기들이 함께 쓴다

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from common.broker.errors import PermanentError, TransientError
from common.env import require_env

logger = logging.getLogger(__name__)

TIMEOUT = 25.0

DATA_GO_KR = "https://apis.data.go.kr"
ECOS = "https://ecos.bok.or.kr/api"


def service_key() -> str:
    """data.go.kr 서비스키. Encoding 형태로 와도 되도록 한 번 푼다.

    포털이 주는 '일반 인증키' 는 `%` 가 섞인 Encoding 형태다. 그대로
    urlencode 하면 `%` 가 `%25` 로 이중 인코딩돼 403 이 난다 (2026-08-29 실측).

    **사용자가 Encoding 과 Decoding 중 어느 쪽을 넣을지 코드가 정할 수 없다.**
    푼 다음 다시 인코딩하면 양쪽 다 통한다.
    """
    return urllib.parse.unquote(require_env("DATA_GO_KR_API_KEY"))


def _fetch(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:200]
        if exc.code >= 500:
            raise TransientError(f"공공 API 서버 오류 (HTTP {exc.code})") from exc
        # 인증키 문제는 재시도로 풀리지 않는다
        raise PermanentError(f"공공 API 거부 (HTTP {exc.code}): {body}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TransientError(f"공공 API 호출 실패: {type(exc).__name__}") from exc


def data_go_kr_json(path: str, **params: Any) -> list[dict[str, str]]:
    """data.go.kr 오퍼레이션을 JSON 으로 부르고 item 목록을 돌려준다."""
    query = urllib.parse.urlencode(
        {"serviceKey": service_key(), "resultType": "json", **params}
    )
    data = json.loads(_fetch(f"{DATA_GO_KR}/{path}?{query}"))
    body = data["response"]["body"]
    items = (body.get("items") or {}).get("item") or []
    return items if isinstance(items, list) else [items]


def data_go_kr_xml(path: str, **params: Any) -> list[dict[str, str]]:
    """data.go.kr 오퍼레이션을 XML 로 부르고 item 목록을 돌려준다.

    관세청은 `resultType=json` 을 무시하고 XML 만 준다 (2026-08-29 실측).
    """
    query = urllib.parse.urlencode({"serviceKey": service_key(), **params})
    root = ET.fromstring(_fetch(f"{DATA_GO_KR}/{path}?{query}"))

    code = root.findtext(".//resultCode")
    if code not in ("00", None):
        raise PermanentError(f"공공 API 오류 {code}: {root.findtext('.//resultMsg')}")

    return [
        {child.tag: (child.text or "") for child in item}
        for item in root.iterfind(".//items/item")
    ]


def ecos_rows(
    stat_code: str, cycle: str, start: str, end: str, item_code: str, count: int
) -> list[dict[str, Any]]:
    """ECOS 통계 조회. 인증키 하나로 모든 통계표를 본다."""
    key = require_env("ECOS_API_KEY")
    url = (
        f"{ECOS}/StatisticSearch/{key}/json/kr/1/{count}"
        f"/{stat_code}/{cycle}/{start}/{end}/{item_code}"
    )
    data = json.loads(_fetch(url))

    if "StatisticSearch" not in data:
        # 결과가 없으면 RESULT 블록으로 온다
        message = data.get("RESULT", {}).get("MESSAGE", str(data)[:150])
        raise PermanentError(f"ECOS 응답에 데이터가 없습니다: {message}")
    return data["StatisticSearch"].get("row", [])
