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

## 분봉 수집

```bash
sudo cp deploy/stock-portal-minute.service deploy/stock-portal-minute.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-portal-minute.timer
```

일 1회 갱신과 타이머를 나눈 이유는 **출처가 다르기 때문**이다.
KRX 일봉은 다음 날 공개되지만 키움 분봉은 당일 바로 받을 수 있다.
한 타이머에 묶으면 분봉이 하루 늦어진다.

관심종목 200개에 약 4분 걸린다. 키움 유량이 1 이라 종목당 1초다.
