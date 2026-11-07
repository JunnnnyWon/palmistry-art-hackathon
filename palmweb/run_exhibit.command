#!/bin/zsh
# 전시 실행: Finder에서 더블클릭.
# 1) 웹 화면을 빌드하고(바뀐 게 있을 때만) 2) 서버를 켜고(죽으면 자동 재시작) 3) Chrome 전시 화면을 연다.
# 전시 중에는 맥이 잠들거나 화면이 꺼지지 않는다.
#
# 종료: 이 터미널 창에서 Ctrl+C (서버·잠자기 방지가 함께 꺼진다). Chrome 전시 화면은 Cmd+Q.
# 환경 변수
#   PALMWEB_KIOSK=0   키오스크(주소창 없는 전체화면) 대신 일반 창으로 연다
#   PALMWEB_QUERY     화면 주소 뒤에 붙일 옵션. 기본 "?room=exhibit&controls=0"
#                     (전시장 방: 노브와 단계를 공유, 화면 단계 버튼은 숨김)
#   PALMWEB_PORT      기본 8000
#   PALMWEB_URL       원격 서버 주소 (예: https://palm.example.com). 주면 이 맥에서 서버를 켜지 않고
#                     그 주소를 키오스크로 연다. 노브도 같은 서버의 exhibit 방에 붙어야 한다.
cd "$(dirname "$0")"
set -e
PORT=${PALMWEB_PORT:-8000}
QUERY="${PALMWEB_QUERY-?room=exhibit&controls=0}"
CHROME_ARGS=(--user-data-dir="$HOME/.palmweb-chrome" --kiosk --no-first-run --noerrdialogs
  --disable-session-crashed-bubble --disable-infobars --overscroll-history-navigation=0)
if [ -n "${PALMWEB_URL:-}" ]; then
  # 원격 서버(GPU 서버 등)를 쓰는 경우: 이 맥은 화면만 띄운다
  caffeinate -dimsu -w $$ &
  if [ "${PALMWEB_KIOSK:-1}" = "1" ]; then open -na "Google Chrome" --args "${CHROME_ARGS[@]}" "${PALMWEB_URL%/}/$QUERY"
  else open -na "Google Chrome" --args --new-window "${PALMWEB_URL%/}/$QUERY"; fi
  echo "원격 서버 화면을 열었습니다: ${PALMWEB_URL%/}/$QUERY  (이 창을 닫으면 잠자기 방지가 꺼집니다)"
  wait
  exit 0
fi
if [ ! -d web/node_modules ]; then (cd web && npm install --no-audit --no-fund); fi
if [ ! -f web/dist/index.html ] || [ -n "$(find web/src web/index.html web/public -newer web/dist/index.html 2>/dev/null)" ]; then
  (cd web && npm run build)
fi
if [ ! -d .venv ]; then uv sync; fi
mkdir -p logs
set +e

# 포트를 이미 다른 서버가 쓰고 있으면 멈춘다. 그대로 두면 새 서버는 3초마다 재시작만 반복하고
# 화면은 기존(예전 코드일 수 있는) 서버에 붙는다. 남의 프로세스는 함부로 끄지 않고 알려 준다.
BUSY=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null | head -1)
if [ -n "$BUSY" ]; then
  echo "포트 $PORT 을 이미 다른 프로그램이 쓰고 있습니다 (PID $BUSY: $(ps -o command= -p $BUSY | cut -c1-80))."
  echo "그 프로그램을 끄거나(kill $BUSY), 다른 포트로 실행하세요: PALMWEB_PORT=8001 ./run_exhibit.command"
  read "?엔터를 누르면 창을 닫습니다..."
  exit 1
fi

# 이 스크립트가 끝나면 이 스크립트가 켠 것(서버 반복 실행, 잠자기 방지)만 끈다
cleanup() {
  trap - EXIT INT TERM
  [ -n "$LOOP" ] && kill $LOOP 2>/dev/null          # 재시작 반복을 먼저 멈추고
  pkill -f "uvicorn server.main:app --host 0.0.0.0 --port $PORT" 2>/dev/null   # 서버를 끈다
  [ -n "$CAFF" ] && kill $CAFF 2>/dev/null
  exit 0
}
trap cleanup EXIT INT TERM

# 전시 중 잠자기·화면 꺼짐 방지 (이 스크립트가 살아 있는 동안만)
caffeinate -dimsu -w $$ &
CAFF=$!

# 서버: 죽으면 3초 뒤 다시 켠다. 출력은 화면과 logs/server.log 에 함께 남긴다.
# 같은 Wi-Fi의 노브(라즈베리파이)가 붙을 수 있게 모든 네트워크 주소에서 받는다.
(
  while true; do
    echo "[$(date '+%H:%M:%S')] 서버 시작" | tee -a logs/server.log
    .venv/bin/uvicorn server.main:app --host 0.0.0.0 --port $PORT 2>&1 | tee -a logs/server.log
    echo "[$(date '+%H:%M:%S')] 서버가 멈췄습니다. 3초 뒤 다시 켭니다" | tee -a logs/server.log
    sleep 3
  done
) &
LOOP=$!

echo "서버 준비 중 (손금 모델을 불러옵니다)..."
for i in {1..90}; do
  if curl -s localhost:$PORT/api/health | grep -q '"unet":"ready"\|unavailable'; then break; fi
  sleep 1
done

URL="http://localhost:$PORT/$QUERY"
if [ "${PALMWEB_KIOSK:-1}" = "1" ]; then
  # 전시 전용 Chrome 프로필: 평소 Chrome과 분리되고, 키오스크 옵션이 확실히 적용된다.
  # 처음 한 번은 이 프로필에서 카메라 권한을 허용해야 한다 (이후 기억됨).
  open -na "Google Chrome" --args "${CHROME_ARGS[@]}" "$URL"
else
  open -na "Google Chrome" --args --new-window "$URL"
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo "노브 주소: http://$IP:$PORT/knob  (라즈베리파이 앱은 이 맥 IP:$PORT 에 붙는다. 다르면 노브 화면 왼쪽 위 구석을 2초 길게 눌러 바꾸기)"
echo "전시 화면 종료는 Chrome에서 Cmd+Q, 서버 종료는 이 창에서 Ctrl+C."
wait $LOOP
