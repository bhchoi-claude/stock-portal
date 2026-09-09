#!/bin/bash
# Phase 8 검증 타이머를 설치한다. 임시 도구다 — 기준이 채워지면 지운다.
#
# 날짜를 박지 않고 평일마다 돈다. 하루 실패해도 다음 날 다시 시도한다.
# 지우려면 이 파일 아래쪽의 안내를 따른다.
set -e

ROOT=/home/bh-server/stock-portal
UNIT=/etc/systemd/system

sudo tee $UNIT/p8test@.service > /dev/null <<SVC
[Unit]
Description=Phase 8 검증 (임시) %i
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=oneshot
User=bh-server
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
# **필요하다.** 파이썬은 스크립트가 있는 디렉터리(deploy/)를 sys.path 에
# 넣지 작업 디렉터리를 넣지 않는다. 없으면 common 을 못 찾는다
Environment=PYTHONPATH=$ROOT
ExecStart=$ROOT/.venv/bin/python $ROOT/deploy/p8test.py %i
StandardOutput=append:$ROOT/logs/p8test.log
StandardError=append:$ROOT/logs/p8test.log
SVC

# 13:30 미체결 만들기 → 15:10 엔진이 취소 → 16:00 차단 → 16:05 검증
for spec in "limit 13:30" "halt 16:00" "verify 16:05"; do
  set -- $spec
  sudo tee $UNIT/p8test-$1.timer > /dev/null <<TMR
[Unit]
Description=Phase 8 $1 (임시)

[Timer]
OnCalendar=Mon..Fri $2 Asia/Seoul
Persistent=false
# **필요하다.** 없으면 타이머가 자기 이름과 같은 p8test-limit.service 를
# 찾다가 'unit to trigger not loaded' 로 시작을 거부한다. 우리는 템플릿을 쓴다
Unit=p8test@$1.service

[Install]
WantedBy=timers.target
TMR
done

# 차단 해제. **15:40 이후라 '장중 재시작 금지' 를 어기지 않는다**
sudo tee $UNIT/p8test-clear.service > /dev/null <<'SVC'
[Unit]
Description=Phase 8 진입 차단 해제 (임시)

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl restart stock-portal-swing
SVC

sudo tee $UNIT/p8test-clear.timer > /dev/null <<'TMR'
[Unit]
Description=Phase 8 차단 해제 타이머 (임시)

[Timer]
OnCalendar=Mon..Fri 16:10 Asia/Seoul
Persistent=false

[Install]
WantedBy=timers.target
TMR

sudo systemctl daemon-reload
sudo systemctl enable --now \
  p8test-limit.timer p8test-halt.timer p8test-verify.timer p8test-clear.timer

systemctl list-timers 'p8test*' --no-pager

# 지울 때:
#   sudo systemctl disable --now p8test-limit.timer p8test-halt.timer \
#     p8test-verify.timer p8test-clear.timer
#   sudo rm /etc/systemd/system/p8test*
#   sudo systemctl daemon-reload
