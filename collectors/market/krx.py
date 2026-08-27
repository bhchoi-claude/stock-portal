# KRX 오픈API 호출 클라이언트. Phase 2 일별매매정보도 이 클라이언트를 쓴다

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from common.env import require_env

# 포털은 openapi.krx.co.kr 이지만 API 호출은 이쪽으로 간다.
# 명세 화면의 샘플에 적힌 Host 표기가 실제와 다르다. openapi 로 부르면 404 다
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"

TIMEOUT = 30.0


class KrxNotAuthorized(RuntimeError):
    """API 이용신청이 안 된 상태. 인증키 문제가 아니므로 재시도해도 소용없다."""


def fetch(path: str, api_id: str, bas_dd: str) -> list[dict[str, Any]]:
    """기준일자 하나로 조회한다. 응답의 목록 블록을 그대로 돌려준다."""
    request = urllib.request.Request(
        f"{BASE_URL}/{path}/{api_id}?basDd={bas_dd}",
        headers={"AUTH_KEY": require_env("KRX_API_KEY")},
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise KrxNotAuthorized(
                f"{api_id} 이용신청이 되어 있지 않습니다."
                " 인증키 발급과 별개로 API 마다 신청해야 합니다."
            ) from exc
        raise

    # 블록 이름이 OutBlock_1 이지만 API 마다 다를 수 있어 목록 값을 찾아 쓴다
    return next((v for v in data.values() if isinstance(v, list)), [])
