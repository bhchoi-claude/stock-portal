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
