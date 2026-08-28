# 배포

systemd 유닛 파일. 서버에서 아래로 설치한다.

```bash
sudo cp deploy/stock-portal-daily.service deploy/stock-portal-daily.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-portal-daily.timer
```

확인.

```bash
systemctl list-timers stock-portal-daily.timer
sudo systemctl start stock-portal-daily.service   # 즉시 1회 실행
tail -f logs/daily.log
```

`.env` 는 `WorkingDirectory` 에서 읽으므로 유닛에 비밀값을 쓰지 않는다.

## 일 1회 갱신 배치

`collectors.market.daily` 는 `price_daily` 에 빠진 최근 거래일을 찾아 채운다.
'오늘' 을 받지 않으므로 실행 시각에 민감하지 않고, 공개가 늦어지면 다음 날
자동으로 따라잡는다. 특정 날짜만 다시 받으려면 인자로 넘긴다.

```bash
.venv/bin/python -m collectors.market.daily 2026-08-26
```

미적재 거래일이 `config/collect.yaml` 의 `max_delay_days` 를 넘게 쌓이면
텔레그램으로 알린다. 조용히 아무것도 하지 않는 상태를 막기 위한 것이다.

## 키움 수집 (분봉·수급)

```bash
sudo cp deploy/stock-portal-kiwoom.service deploy/stock-portal-kiwoom.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-portal-kiwoom.timer
```

일 1회 갱신과 타이머를 나눈 이유는 **출처가 다르기 때문**이다.
KRX 일봉은 다음 날 공개되지만 키움 분봉·수급은 당일 바로 받을 수 있다.
한 타이머에 묶으면 하루 늦어진다.

분봉과 수급은 한 유닛에 넣었다. 출처와 실행 시각이 같고 서로 의존하지 않는다.
`ExecStart=-` 로 앞이 실패해도 뒤가 돈다.

**파티션 생성이 맨 앞이다.** `price_minute` 는 월 파티션이 없으면 적재가
실패한다. 여기만 `-` 를 붙이지 않아서, 실패하면 뒤가 돌지 않는다.

관심종목 200개 기준 분봉 약 4분, 수급 약 4분이다. 키움 유량이 1 이라
종목당 1초가 하한이다.
