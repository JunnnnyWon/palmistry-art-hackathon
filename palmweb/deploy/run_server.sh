#!/usr/bin/env bash
# GPU 서버(리눅스)용 실행 스크립트: 서버가 죽으면 3초 뒤 다시 켠다.
#   setsid nohup ~/palmistry-art/palmweb/deploy/run_server.sh > /dev/null 2>&1 &
# 재부팅 뒤 자동 시작은 deploy/palmweb.service (systemd 사용자 서비스) + `sudo loginctl enable-linger $USER` 로.
set -u
cd "$(dirname "$0")/.."
mkdir -p logs
# mediapipe가 쓰는 libGLESv2: 시스템에 없으면 사용자 폴더에 받아 둔 것을 쓴다 (sudo apt install libgles2 하면 불필요)
if [ -d "$HOME/palmistry-art/syslib/root/usr/lib/x86_64-linux-gnu" ]; then
  export LD_LIBRARY_PATH="$HOME/palmistry-art/syslib/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
export PALMWEB_WORKERS="${PALMWEB_WORKERS:-4}"
HOST="${PALMWEB_HOST:-0.0.0.0}"
PORT="${PALMWEB_PORT:-8000}"
echo $$ > logs/run_server.pid
while true; do
  echo "[$(date '+%F %T')] 서버 시작 ($HOST:$PORT, 워커 $PALMWEB_WORKERS)" >> logs/server.log
  .venv/bin/uvicorn server.main:app --host "$HOST" --port "$PORT" >> logs/server.log 2>&1
  echo "[$(date '+%F %T')] 서버가 멈췄습니다. 3초 뒤 다시 켭니다" >> logs/server.log
  sleep 3
done
