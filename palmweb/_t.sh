#!/bin/zsh
# 전시 실행: Finder에서 더블클릭.
# 1) 웹 화면을 빌드하고(바뀐 게 있을 때만) 2) 서버를 켜고(죽으면 자동 재시작) 3) Chrome 전시 화면을 연다.
# 전시 중에는 맥이 잠들거나 화면이 꺼지지 않는다.
#
# 종료: 이 터미널 창에서 Ctrl+C (서버·잠자기 방지가 함께 꺼진다). Chrome 전시 화면은 Cmd+Q.
# 환경 변수
#   PALMWEB_KIOSK=0   키오스크(주소창 없는 전체화면) 대신 일반 창으로 연다
#   PALMWEB_QUERY     화면 주소 뒤에 붙일 옵션 (예: "?cam=macbook")
#   PALMWEB_PORT      기본 8000
cd "$(dirname "$0")"
set -e
PORT=${PALMWEB_PORT:-8000}
if [ ! -d web/node_modules ]; then (cd web && npm install --no-audit --no-fund); fi
if [ ! -f web/dist/index.html ] || [ -n "$(find web/src web/index.html web/public -newer web/dist/index.html 2>/dev/null)" ]; then
  (cd web && npm run build)
fi
if [ ! -d .venv ]; then uv sync; fi
mkdir -p logs
set +e

# 이 스크립트가 끝나면 함께 켠 것(서버 반복 실행, 잠자기 방지)을 모두 끈다
cleanup() { trap - EXIT INT TERM; kill 0 2>/dev/null; }
trap cleanup EXIT INT TERM

# 전시 중 잠자기·화면 꺼짐 방지 (이 스크립트가 살아 있는 동안만)
caffeinate -dimsu -w $$ &

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

echo "서버 준비 중 (손금 모델을 불러옵니다)..."
for i in {1..90}; do
  if curl -s localhost:$PORT/api/health | grep -q '"unet":"ready"\|unavailable'; then break; fi
  sleep 1
done

URL="http://localhost:$PORT/${PALMWEB_QUERY:-}"
if [ "${PALMWEB_KIOSK:-1}" = "1" ]; then
  # 전시 전용 Chrome 프로필: 평소 Chrome과 분리되고, 키오스크 옵션이 확실히 적용된다.
  # 처음 한 번은 이 프로필에서 카메라 권한을 허용해야 한다 (이후 기억됨).
  echo "[테스트] Chrome 키오스크 생략" #

else
  echo "[테스트] Chrome 생략"
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo "노브 주소: http://$IP:$PORT/knob  (라즈베리파이 앱은 이 맥 IP:$PORT 에 붙는다. 다르면 노브 화면 위쪽을 길게 눌러 바꾸기)"
echo "전시 화면 종료는 Chrome에서 Cmd+Q, 서버 종료는 이 창에서 Ctrl+C."
sleep 25
