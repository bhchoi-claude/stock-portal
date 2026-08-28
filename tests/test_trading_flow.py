# 키움 수급 응답 파싱을 실제 응답 샘플로 고정해 확인한다

from datetime import date
from decimal import Decimal

from common.broker.kiwoom import KiwoomBroker

# 2026-08-27 삼성전자 ka10059 응답에서 그대로 옮긴 행이다
FLOW_ROW = {
    "dt": "20260827",
    "cur_prc": "+266000",
    "pre_sig": "2",
    "pred_pre": "+4500",
    "flu_rt": "+172",
    "acc_trde_qty": "16829395",
    "acc_trde_prica": "4488160",
    "ind_invsr": "-862106",
    "frgnr_invsr": "374719",
    "orgn": "-25200",
    "fnnc_invt": "91620",
    "insrnc": "9811",
    "invtrt": "-54236",
    "etc_fnnc": "-876",
    "bank": "1451",
    "penfnd_etc": "-32574",
    "samo_fund": "-40396",
    "natn": "0",
    "etc_corp": "517325",
    "natfor": "-4738",
}


def test_수급_한_행_매핑():
    flow = KiwoomBroker._to_flow("KRX:005930", FLOW_ROW)

    assert flow.stock_id == "KRX:005930"
    assert flow.trade_date == date(2026, 8, 27)


def test_백만원을_원으로_바꾼다():
    # 응답은 백만원 단위다. price_daily.value 가 원이라 맞춘다
    flow = KiwoomBroker._to_flow("KRX:005930", FLOW_ROW)

    assert flow.foreign_net == Decimal(374719) * 1_000_000
    assert flow.individual_net == Decimal(-862106) * 1_000_000


def test_부호를_벗기지_않는다():
    # 분봉 가격과 달리 여기서 마이너스는 순매도라는 뜻이다
    flow = KiwoomBroker._to_flow("KRX:005930", FLOW_ROW)

    assert flow.individual_net < 0
    assert flow.institution_net < 0
    assert flow.foreign_net > 0


def test_순매수_합이_영이다():
    # 개인 + 외국인 + 기관 + 기타법인 + 내외국인 = 0 이어야 한다.
    # 매수한 만큼 누군가 팔았다
    total = sum(
        int(FLOW_ROW[f])
        for f in ("ind_invsr", "frgnr_invsr", "orgn", "etc_corp", "natfor")
    )

    assert total == 0


def test_기관_세부가_기관_합계와_맞는다():
    detail = sum(
        int(FLOW_ROW[f])
        for f in (
            "fnnc_invt",
            "insrnc",
            "invtrt",
            "etc_fnnc",
            "bank",
            "penfnd_etc",
            "samo_fund",
            "natn",
        )
    )

    assert detail == int(FLOW_ROW["orgn"])
