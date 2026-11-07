#!/usr/bin/env bash
# Cloudflare Tunnel로 https://<도메인> → 이 서버의 palmweb(localhost:8000) 연결.
# 공유기 포트포워딩 없이, 관리자 권한 없이 동작한다. 인증서는 Cloudflare가 처리한다.
#
# 준비 (한 번):
#   1) cloudflared 를 ~/palmistry-art/bin/cloudflared 에 둔다 (README 참고, 공식 GitHub 배포본 + 체크섬 확인)
#   2) ~/palmistry-art/bin/cloudflared tunnel login   → 나오는 주소를 브라우저로 열어 junnnny.kr 선택
# 실행:
#   ~/palmistry-art/palmweb/deploy/setup_tunnel.sh hackathon.junnnny.kr
# 다시 실행해도 된다 (터널이 있으면 재사용, DNS는 덮어씀, 실행 중인 터널은 새로 띄움).
set -euo pipefail
CF="$HOME/palmistry-art/bin/cloudflared"
NAME="palmweb"
HOST="${1:-hackathon.junnnny.kr}"
PORT="${PALMWEB_PORT:-8000}"
LOGDIR="$HOME/palmistry-art/palmweb/logs"
mkdir -p "$LOGDIR"

[ -x "$CF" ] || { echo "cloudflared 가 없습니다: $CF"; exit 1; }
[ -f "$HOME/.cloudflared/cert.pem" ] || { echo "먼저 로그인하세요: $CF tunnel login"; exit 1; }

# 1) 터널 (없으면 만든다)
if ! "$CF" tunnel info "$NAME" >/dev/null 2>&1; then
  "$CF" tunnel create "$NAME"
fi
ID=$("$CF" tunnel list -o json | python3 -c "import json,sys; print(next(t['id'] for t in json.load(sys.stdin) if t['name']=='$NAME'))")
echo "터널 $NAME = $ID"

# 2) 설정: 이 도메인 → localhost 의 palmweb. 나머지는 404
cat > "$HOME/.cloudflared/palmweb.yml" <<CFG
tunnel: $ID
credentials-file: $HOME/.cloudflared/$ID.json
ingress:
  - hostname: $HOST
    service: http://localhost:$PORT
  - service: http_status:404
CFG

# 3) DNS: <도메인> → 터널 (Cloudflare에 CNAME 레코드를 만든다. 있으면 덮어씀)
"$CF" tunnel route dns --overwrite-dns "$NAME" "$HOST"

# 4) 실행: 죽으면 3초 뒤 다시. 이미 돌고 있으면 정리하고 새로 띄운다
pkill -f "[p]almweb-tunnel-loop" 2>/dev/null || true
pkill -f "[c]loudflared tunnel --config $HOME/.cloudflared/palmweb.yml" 2>/dev/null || true
setsid nohup bash -c "exec -a palmweb-tunnel-loop bash -c 'while true; do \"$CF\" tunnel --config \"$HOME/.cloudflared/palmweb.yml\" run \"$NAME\" >> \"$LOGDIR/tunnel.log\" 2>&1; sleep 3; done'" \
  >/dev/null 2>&1 < /dev/null &
sleep 5
echo "터널 실행 중. 기록: $LOGDIR/tunnel.log"
echo "접속 주소: https://$HOST"
