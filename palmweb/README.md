# palmweb

손금 웹 체험. 브라우저가 카메라로 손바닥을 찍어 서버로 보내고, 서버는 [palmlines](../palmlines/)로 손금을 읽어 돌려준다.
설계는 [손금_웹체험_개발명세서.md](../손금_웹체험_개발명세서.md). 현재 W5까지 구현: 대기 화면 → 촬영 → 3D 손 위 손금 → AI 해석 자막 → 단계마다 손금에서 붉은 혈관이 반투명 손 안으로 퍼지며 펄스가 핏줄을 따라 흐르고, 손이 조금씩 커져 10단계에 손 전체 장악 (손이 뛰는 박동과 자막 흔들림은 2026-11-07 제거) → 90초 무입력이면 대기 화면으로.

## 실행 (전시)

Finder에서 `run_exhibit.command`를 더블클릭한다.

1. 웹 화면을 빌드한다 (바뀐 게 있을 때만).
2. 서버를 켠다. 서버가 죽으면 3초 뒤 자동으로 다시 켠다. 기록은 `logs/server.log`.
3. 맥이 잠들거나 화면이 꺼지지 않게 한다 (스크립트가 켜져 있는 동안만).
4. Chrome을 키오스크 모드(주소창 없는 전체화면)로 연다. 평소 Chrome과 분리된 전시 전용 프로필(`~/.palmweb-chrome`)을 쓴다.
   **처음 한 번은 이 프로필에서 카메라 권한을 허용해야 한다.** 이후에는 기억된다.

종료: 전시 화면은 Cmd+Q, 서버와 잠자기 방지는 터미널 창에서 Ctrl+C.

| 환경 변수 | 뜻 |
| --- | --- |
| `PALMWEB_KIOSK=0` | 키오스크 대신 평소 Chrome의 일반 창으로 연다 |
| `PALMWEB_QUERY` | 화면 주소 뒤에 붙일 옵션. 기본 `?room=exhibit&controls=0` (`controls=0` 은 이제 효과 없음: 화면 단계 패널은 2026-11-07 제거) |
| `PALMWEB_URL=https://…` | 원격 서버(GPU 서버) 화면을 연다. 이 맥에서는 서버를 켜지 않는다 |
| `PALMWEB_PORT` | 기본 8000 |

## 여러 관객과 GPU 서버 (2026-11-06)

여러 관객이 각자 진행한다. 단계 상태는 "방(room)" 단위로 나뉜다.

| 접속 | 방 | 단계 조작 |
| --- | --- | --- |
| 일반 관객 `https://<서버>/` | 접속마다 개인 방 (`solo-…`) | 방향키 |
| 전시장 화면 `https://<서버>/?room=exhibit&controls=0` | `exhibit` | 노브(라즈베리파이)와 방향키 |
| 노브 `/ws/control` | 방을 지정하지 않으면 `exhibit` | 다이얼 |
| 전시 화면 `/r1`, `/r2` … | `r1`, `r2` … (`/?room=r1&controls=0` 으로 넘어감) | 짝이 되는 노브 |
| 노브 화면 `/n1`, `/n2` … (아이패드 등) | `r1`, `r2` … | 다이얼 |

손 추적은 브라우저가 직접 한다 (MediaPipe 웹 버전, wasm과 모델은 우리 서버에서 받음). 캡처 조건을 만족하는 순간
사진 5장만 서버로 보낸다. 관객당 업로드가 초당 약 18Mbps에서 캡처 1회 약 0.75MB로 줄었다.
브라우저에서 MediaPipe를 못 띄우면 자동으로 서버 추적(예전 방식)으로 돌아간다. `?track=server`로 강제할 수 있다.

서버는 손금 추출을 워커 여러 개(`PALMWEB_WORKERS`, 기본 CPU÷4, 최대 4)로 동시에 처리하고,
NVIDIA GPU가 있으면 U-Net을 GPU에서 돌린다. OpenRouter 동시 요청은 서버 전체 20개(`PALMWEB_LLM_CONCURRENCY`)로 묶는다.

### GPU 서버 배포 (`junnnny`, Tailscale 100.90.117.108)

코드 위치 `~/palmistry-art/` (palmlines, palmweb, .env). 이 맥에서 화면을 빌드한 뒤 동기화한다.

```bash
cd ~/Desktop/예술해커톤/palmweb/web && npm run build
```

```bash
cd ~/Desktop/예술해커톤 && rsync -a -e "ssh -o BatchMode=yes" --exclude='.venv/' --exclude='__pycache__/' --exclude='node_modules/' --exclude='.git/' --exclude='.DS_Store' --exclude='/palmlines/out/' --exclude='/palmlines/captures/' --exclude='/palmlines/tests/' --exclude='/palmweb/logs/' --exclude='/palmweb/sessions/' palmlines palmweb junnnny@100.90.117.108:palmistry-art/
```

서버 실행(재시작 반복, SSH가 끊겨도 유지):

```bash
ssh junnnny@100.90.117.108 'cd ~/palmistry-art/palmweb && ~/.local/bin/uv sync && setsid nohup ./deploy/run_server.sh >/dev/null 2>&1 </dev/null &'
```

서버 코드를 바꿨으면 아래처럼 서버만 끈다. 실행 스크립트가 3초 뒤 새 코드로 다시 켠다.
`[u]` 처럼 대괄호를 쓰는 이유는, 그냥 `uvicorn server.main` 이라고 쓰면 이 명령을 실행한 SSH 셸까지 함께 꺼지기 때문이다.

```bash
ssh junnnny@100.90.117.108 'pkill -f "[u]vicorn server.main"'
```
재부팅 뒤 자동 시작은 `deploy/palmweb.service` 설명대로 systemd 사용자 서비스로 등록한다 (관리자 권한 한 번 필요).

### 도메인 연결 (https://hackathon.junnnny.kr, Cloudflare Tunnel) — 2026-11-06 연결 완료

`junnnny.kr`의 DNS는 Cloudflare에 있다. GPU 서버에서 Cloudflare Tunnel을 돌려 공유기 포트포워딩 없이 HTTPS로 연결한다.
서버에서 한 번만 실행한다 (cloudflared는 GitHub 공식 배포본, 체크섬 확인):

```bash
mkdir -p ~/palmistry-art/bin && cd ~/palmistry-art/bin && curl -sSL -o cloudflared https://github.com/cloudflare/cloudflared/releases/download/2026.10.7/cloudflared-linux-amd64 && echo "1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872  cloudflared" | sha256sum -c - && chmod +x cloudflared
```

```bash
~/palmistry-art/bin/cloudflared tunnel login
```

나오는 주소를 브라우저로 열어 Cloudflare에 로그인하고 `junnnny.kr`을 고른다. 그다음:

```bash
~/palmistry-art/palmweb/deploy/setup_tunnel.sh hackathon.junnnny.kr
```

터널 만들기, DNS 연결(CNAME), 실행(죽으면 재시작)까지 한다. 기록은 `logs/tunnel.log`.
`cloudflared tunnel login`은 약 10분만 승인을 기다린다. 넘기면 다시 실행해 새 주소로 승인한다.
전시장 맥에서는 `PALMWEB_URL=https://hackathon.junnnny.kr ./run_exhibit.command` 로 이 주소를 키오스크로 연다.

동시 관객 시험:

```bash
uv run python -m server.multi_test --visitors 16 --url ws://100.90.117.108:8000
```

## "여기서 멈춤" 버튼과 대시보드 (2026-11-07)

시연자가 1~10단계를 넘기며 "어느 단계부터 불쾌한가"를 묻고, 관객은 그 단계에서 화면 오른쪽 위의 **여기서 멈춤** 버튼을 누른다.

1. 버튼(또는 Space)을 누르면 멈춘 단계와 그 순간 AI가 하던 글이 뜨고, 닉네임을 입력해 **제출**한다 (Enter 로도 제출, Esc·"계속 보기"로 취소).
2. 서버가 `sessions/submissions/<id>.json` (단계, 글, 닉네임, 단계별 머문 시간, 손금 묘사) 과 `<id>.jpg` (그 순간의 3D 손. 손이 차지하는 영역만 잘라 원본 해상도로) 로 저장하고 대시보드에 바로 알린다.
3. "기록되었습니다" 가 약 4초 보인 뒤 촬영 화면으로 돌아간다. 다음 관객은 그대로 손을 보여 주면 된다. 새로 고침해도 촬영 화면에서 시작한다.

대시보드는 같은 서버의 **`/dashboard`** (다른 노트북·모니터에서 열어도 된다). 흰 바탕, 한 화면에 다 들어가고 페이지 스크롤이 없다 (치수가 vh·vw 비율이라 해상도·세로 화면에서도 구도가 같다).
구도는 손금 패널 | 캡차 패널 반반, 두 패널이 같은 틀(대칭). 각 패널은 같은 크기의 정사각형 검은 틀(손 스냅샷은 틀 안에 맞추고, 합친 그림은 꽉 찬다) 옆에 이름(감정 / 이름 두 줄), 손금 쪽은 그 아래 멈춘 단계(붉은 숫자)만. 아래에 같은 규격의 정사각형 띠(손금 띠엔 단계 숫자 배지, 이름은 툴팁).
틀 크기는 JS 가 정한다: 자리 높이와 (폭 − 이름 칸) 중 작은 쪽을 두 패널에 같이 준다. 띠는 들어가는 만큼만 보이고 나머지는 숨긴다. 카드를 누르면 고정(Esc 해제), 아니면 새 기록이 오는 대로 바뀐다. 세로 화면이면 손금 위·캡차 아래.
진행 중 표시("대기 중", "OO · 고르는 중")는 두지 않는다 (서버는 `live`·`captcha_live` 를 계속 보내지만 화면은 쓰지 않는다).
이름은 입력받지 않는다. 카카오톡 익명 프로필처럼 서버(`server/names.py`)가 "감정 + 이름"(행복한 원준, 신난 승우, 화난 진복 …; 이름은 원준·다은·승우·정민·진복)을 무작위로 붙인다. 손금 체험은 손금을 읽기 시작할 때 붙여 체험 화면 오른쪽 위·제출 창·대시보드에 같은 이름이 뜨고, 캡차는 회차마다 새 이름을 받아 머리띠에 보인다.
새 기록(손금 제출·캡차 응답)이 오면 "띠링" 알림음이 난다 (Web Audio 합성, 파일 없음). 브라우저가 첫 클릭 전까지 소리를 막으므로 헤더의 🔔 버튼이 "클릭해서 소리 켜기"로 깜빡인다: 전시 전 대시보드 화면을 한 번 클릭할 것. 버튼으로 끄면 브라우저에 기억된다.
위는 지금 체험 중인 화면의 단계(실시간)·인원과 **초기화** 버튼, 왼쪽은 최근 기록 카드(화면에 들어가는 만큼, 나머지는 "외 N명"),
오른쪽은 고른 기록(닉네임, 멈춘 단계, 손 스냅샷, **1단계 글** 전체). 멈춘 단계의 무너진 글은 저장만 하고(`text`) 화면에는 온전한 1단계 글(`text_step1`)을 띄운다. 글은 남는 높이에 맞춰 글자 크기가 맞춰진다.
카드를 클릭하면 상세가 고정되고 Esc 로 최신 기록 따라가기로 돌아온다.
초기화는 두 번 확인하며, 기록을 지우지 않고 `sessions/submissions/archive-<시각>/` 으로 옮긴다 (40명분 집계에 쓴다).

| 주소 | 뜻 |
| --- | --- |
| `POST /api/submit` | 체험 화면의 제출 |
| `GET /api/submissions` | 제출 목록 (최신순 JSON). `/api/submissions/<id>/image` 는 스냅샷 |
| `DELETE /api/submissions` | 초기화 (보관함으로 이동) |
| `WS /ws/dashboard` | `init`(items 전체 + captcha 전체) → `submission`(새 기록) · `captcha`(새 캡차 응답) · `live`(체험 화면의 관객 이름·단계) · `captcha_live`(캡차 화면의 관객 이름·고른 수) · `reset` |

e2e 점검(헤드리스 Chromium, 체험 → 제출 → 대시보드 → 초기화, 해상도 5종 스크롤·잘림 검사)은 `tools/e2e_qa.py` (파일 머리의 실행법 참고). 마지막에 초기화를 누르므로 전시 중인 서버에는 돌리지 않는다.

## 예술 캡차 (`/captcha`, 2026-11-07)

"매력을 느끼는 '손'을 선택하세요". 익숙한 캡차 카드 모양(화이트 모노톤: 흰 카드, 검은 사진 9장, 검은 확인 버튼)이지만 정답이 없고, 고른 손들이 한 장의 그림으로 합쳐진다.
`web/public/captcha/manifest.json` 의 `round` 에 적힌 고정 9장(손 5: 손바닥·노인·아이·어두운 피부·반지, 손이 아닌 것 4: 핏줄 손·X선·유리 손·불가사리)을 회차마다 자리만 섞어 보여준다. 선택 수 제한 없음.
확인을 누르면 격자 자리에 고른 장들을 하나로 합친 그림이 뜬다. 그림은 511가지 조합(2^9-1)을 `tools/gen_captcha_merge.py` 로 미리 만들어 둔 것이라(`web/public/captcha/merge/<mask>.jpg`, mask = round 순서대로 고른 칸 1·아닌 칸 0) 바로 나온다.
칸을 누를 때마다 그 조합의 그림을 미리 받아 두므로 확인을 누르는 순간 0초. 합성은 OpenRouter 이미지 API(Seedream 5 flash, 한 장 0.018달러, 511장 ≈ 9달러)에 고른 장들을 참조 이미지로 넣어 "GAN 잠재 공간에서 교배한 듯한 하나의 손, 유화 질감"으로 뽑는다 (Alexander Reben의 amalGAN 연작 참고).
서버는 `/captcha/merge/` 를 `web/public/captcha/merge/` 에서 직접 서빙하므로(dist 와 별개) 서버에서 생성해도 빌드 없이 바로 보인다. `round` 를 바꾸면 조합 그림을 다시 만들어야 한다.
응답은 `POST /api/captcha` 로 `sessions/captcha/<id>.json` 에 남는다 (어느 칸을 골랐는지, mask, 걸린 시간). 키: Enter 확인, R 다시 섞기, F 전체화면.
30장 원본 이미지는 `tools/gen_captcha.py` 로 OpenRouter(gpt-image)에서 뽑았고, 이름을 주면 그 장만 다시 뽑는다 (파일 머리 참고).

## 발표 자료(구글 슬라이드) 2쪽에 대시보드 띄우기 (`tools/slides_live.py`, 2026-11-07)

전시 발표 자료(구글 슬라이드)에서 발표하면서 2쪽 안에 대시보드가 보여야 한다. 구글 슬라이드엔 웹페이지를 넣을 수 없으므로, 대시보드를 몇 초마다 스크린샷으로 찍어
슬라이드 API로 2쪽의 목업 이미지를 갈아 끼운다. 발표 모드는 문서 변경을 따라 다시 그리므로 "움직이는 그림"처럼 보인다 (3~5초 지연, 소리·클릭 없음).

준비 (한 번, 발표 자료 편집 권한이 있는 구글 계정으로):
1. https://console.cloud.google.com → 프로젝트 만들기 → "API 및 서비스 → 라이브러리" 에서 **Google Slides API** 사용 설정.
2. "IAM 및 관리자 → 서비스 계정" → 만들기 (역할 없음) → 그 계정의 "키" 탭 → 키 추가 → JSON → 내려받기.
3. 내려받은 JSON 을 저장소 최상위에 `.google-sa.json` 으로 두고 (git 제외) 서버에 올린다: `scp .google-sa.json junnnny@100.90.117.108:palmistry-art/`
4. 발표 자료 "공유" 에 서비스 계정 이메일(`…@….iam.gserviceaccount.com`)을 **편집자**로 추가.

실행 (서버):
```bash
ssh junnnny@100.90.117.108 'cd ~/palmistry-art/palmweb && .venv/bin/python tools/slides_live.py --list'      # 2쪽 요소 확인
ssh junnnny@100.90.117.108 'cd ~/palmistry-art/palmweb && .venv/bin/python tools/slides_live.py --once'      # 한 번 교체해 보기
ssh junnnny@100.90.117.108 'cd ~/palmistry-art/palmweb && setsid nohup .venv/bin/python tools/slides_live.py > logs/slides_live.log 2>&1 < /dev/null &'   # 계속
ssh junnnny@100.90.117.108 'pkill -f slides_live.py'   # 끝내기
```
스크린샷은 헤드리스 크롬(Playwright, `uv sync --group live` + `playwright install chromium`)으로 `/dashboard?shot=1`(버튼 숨김)을 1920×1080 으로 찍어 `web/public/live/dash-<시각>.jpg` 에 두고(`/live/` 로 서빙, 60초 뒤 삭제),
구글이 그 공개 주소에서 그림을 가져간다. 대상 쪽(기본 6쪽)에 쪽 전체 크기의 이미지(ID `live_…`)를 만들어 두고 그것만 교체한다. 환경 변수 `SLIDES_DECK`(자료 ID)·`SLIDES_PAGE`(쪽)·`SLIDES_INTERVAL`(확인 간격 초)·`SLIDES_FORCE`(강제 교체 초)·`SLIDES_SIZE`(스크린샷 크기)·`SLIDES_QUALITY`(JPEG 품질)로 바꾼다.
속도: 기록이 들어오면 0.25초 안에 알아채고 스크린샷(0.1초) + 구글 API(약 1.5초, 구글이 그림을 받아가 처리하는 시간이 대부분)로 2초 안에 교체가 끝난다. 그 뒤 발표자 모드에 반영되는 몇 초는 구글 몫. 그림은 1600×900 JPEG(117KB, 1920×1080 보다 0.4초 빠름)이고 `?shot=1` 에서는 전환 효과를 꺼 전환 중간 장면을 보내지 않는다.
부하: 0.25초마다 찍되 구글 API 는 화면이 바뀌었을 때만 부른다(작은 회색 그림 평균 차이 < 1 이면 건너뜀, 30초에 한 번은 강제). 대시보드는 새 기록이 올 때만 바뀌므로 실제 호출은 분당 몇 번이고
쓰기 한도(계정당 분당 60회)와 거리가 멀다. 서버 쪽은 루프 CPU 0.5%, 헤드리스 크롬 포함 메모리 300MB 안팎, 그림 한 장 200KB.
글꼴: 서버에 한글 폰트가 없어 `~/.fonts` 에 Pretendard(가변)·Noto Sans KR·JetBrains Mono 를 두고 `~/.config/fontconfig/fonts.conf` 로 맥 글꼴 이름(-apple-system, Apple SD Gothic Neo, SF Mono, Menlo …)을 거기에 이었다.
**확인됨**: 편집 권한 계정으로 연 발표자 모드는 교체를 따라 다시 그린다(새 기록이 오면 2~5초 안에 바뀜). 로그인하지 않은 브라우저의 발표 모드는 새로 열어야만 보이므로, 전시 때는 반드시 편집 권한 계정으로 로그인해 발표자 모드를 연다. 진단용 시계 글 상자(ID `livetxt_…`)를 두면 매 교체마다 시각이 바뀐다(없으면 무시).

## 운영 흐름

| 화면 | 언제 | 무엇이 보이나 |
| --- | --- | --- |
| 대기 | 손이 4초 동안 안 보일 때 | 빈 3D 손이 천천히 돌고 "손바닥을 카메라에 보여주세요" |
| 촬영 | 손이 보이면 바로 | 카메라 화면, 손 랜드마크, 안내와 게이지. 약 1초 멈추면 캡처 |
| 결과 | 캡처 후 | 3D 손 위 손금, 해석 자막, 단계(방향키·노브)에 따라 뿌리 확산 |
| 정리 | 결과 화면에서 방향키·노브 입력이 90초 없을 때 | 뿌리가 걷히고 손금이 3초에 걸쳐 사라진 뒤 대기 화면 |

R 키는 언제든 바로 처음(촬영)으로 돌아간다. 카메라가 끊기면 2초마다 다시 열고, 3D 그래픽 장치를 잃으면 페이지를 새로 고친다.

준비물: Node.js, uv, Google Chrome, palmlines 모델 파일 (palmlines/README.md의 "준비" 참고),
그리고 저장소 최상위 `.env`의 `OPENROUTER_API_KEY` (AI 해석용. 없으면 손금까지만 나오고 자막에 안내가 뜬다).

## 노브 (라즈베리파이 터치스크린)

같은 Wi-Fi 기기에서 `http://<맥 IP>:8000/knob`을 열면 해석 단계 노브가 뜬다. 라즈베리파이(Tizen) 설치는 [knob/README.md](knob/README.md).
노브와 방향키는 서버의 같은 단계 값을 바꾸므로 어느 쪽으로 돌려도 양쪽이 같이 움직인다.

## 개발

터미널 두 개를 쓴다. 코드를 고치면 바로 반영된다.

```bash
cd palmweb && uv sync && uv run uvicorn server.main:app --port 8000 --reload
```

```bash
cd palmweb/web && npm install && npm run dev
```

브라우저에서 `http://localhost:5173`을 연다. Vite가 `/ws`, `/api`, `/samples`를 8000번 서버로 넘긴다.

## 주소 옵션

| 주소 | 동작 |
| --- | --- |
| `/?src=/samples/hand70.jpg` | 웹캠 대신 점검용 사진으로 실행 |
| `/?cam=macbook` | 이름에 "macbook"이 들어간 카메라를 우선 선택 |
| `/?view=2d` | 결과를 3D 손 대신 평면으로 보기 |
| `/?fx=0` | 블룸 후처리 끄기 (느린 기기용) |
| `/dev` | 테스트 화면: 저장된 손금(web/public/dev/palm.json)을 바로 띄우고, 핏줄·손금 색(hex)과 거칠기·환경광·핏줄 세기를 패널에서 바꿔 본다. 카메라·서버·해석 없음, 단계는 패널 버튼·방향키 |
| `/?src=/testset/<폴더>/frame_0.png` | 로컬 웹캠 테스트 사진으로 실행 (palmlines/tests/testset 이 있을 때) |

카메라는 처음에 iPhone 연속성 카메라·데스크뷰·가상 카메라를 피해 내장 카메라를 고르고, 고른 카메라를 기억한다.

## 키

| 키 | 동작 |
| --- | --- |
| ← → | 해석 단계 1~10 (temperature 0.1~1.9) |
| R | 다시 찍기 |
| Space | 촬영 화면: 손바닥이 보일 때 바로 캡처 · 결과 화면: "여기서 멈춤" |
| C | 다음 카메라로 바꾸기 |
| 2 | 결과 화면을 3D 손 ↔ 평면으로 전환 |
| D | 디버그 정보 (처리 속도, 입력 해상도, 모델 상태) |
| F | 전체화면 |

## 구조

```
server/main.py        FastAPI: /api/health, /ws/track, /api/submit·/ws/dashboard·/dashboard (멈춤 기록), /captcha·/captcha/merge·/api/captcha (캡차), /samples, 빌드된 화면
server/palmistry.py   손금 특징 계산 → 한국어 묘사 (AI 입력)
server/llm.py         시스템 프롬프트, 10단계 샘플링 설정, OpenRouter 병렬 생성
server/smoke_test.py  서버 점검: 사진을 프레임처럼 보내 자동 캡처까지 확인
server/soak_test.py   리허설 부하 시험: 관객 사이클(캡처 → 해석 → 노브 조작 → 다시 찍기)을 정해진 시간 반복
server/multi_test.py  여러 관객 동시 진행 시험 (개인 방, 캡처 묶음, 단계 섞임 여부)
deploy/               GPU 서버용 실행 스크립트(run_server.sh)와 systemd 사용자 서비스 파일
knob/index.html       노브 화면 (한 파일, Tizen 옛 웹 엔진 호환). 서버의 /knob
knob/tizen/config.xml Tizen 웹 앱 설정
tools/build_knob_wgt.py 노브를 Tizen 앱으로 묶기 (--install: 서명·설치·실행)
tools/e2e_qa.py      e2e 점검 (Playwright)
tools/gen_captcha.py 예술 캡차 이미지 30장 생성 (OpenRouter gpt-image, web/public/captcha/)
tools/gen_captcha_merge.py 캡차 고정 9장의 모든 조합(511) 합성 그림 사전 생성 (OpenRouter Seedream, web/public/captcha/merge/)
web/src/
  main.ts             상태 머신 (촬영 → 읽는 중 → 결과)과 키 입력
  source.ts           웹캠(좌우 반전) / 점검용 사진 입력, 카메라 선택
  connection.ts       /ws/track 연결, 끊기면 자동 재연결
  capture.ts          촬영 화면 그리기, 프레임 전송 (응답 후 다음 프레임, 초당 최대 15장)
  hand3d.ts           3D 손 장면: 실사 텍스처 손, 황금 손금, 피부 밑에 비치는 모델링 핏줄(별도 패스 합성), 성장·고동, 별 배경, 블룸, 드래그 회전
  spread.ts           뿌리 확산 계산 (손금 씨앗 → 메시 최단거리 도착 시간, 뿌리 나무)
  projection.ts       손금 표준 좌표 → 3D 손 표면 (thin-plate spline + 광선)
  result2d.ts         결과 2D 렌더 (2 키)
  subtitle.ts         자막 (타이핑, 3줄 넘으면 위로 흘림. 외국 문자는 줄 높이를 고정해 들썩이지 않게)
  dashboard.ts/.css   대시보드 화면 (web/dashboard.html → 서버의 /dashboard)
  captcha.ts/.css     예술 캡차 화면 (web/captcha.html → 서버의 /captcha)
  tracker.ts          브라우저 손 추적 (MediaPipe 웹 버전)
  gate.ts             캡처 판정 (palmlines/hand.py 의 판정을 옮긴 것)
  protocol.ts         서버 메시지 형식, 선 색·이름
web/public/models/    가공된 손 모델(hand.glb)과 관절 위치(hand.joints.json)
tools/prepare_hand.py 손 모델 가공 스크립트
assets/hand/          원본 손 모델 (WebXR generic-hand, MIT)과 라이선스
sessions/             캡처마다 손금 JSON 저장 (사진은 저장 안 함). PALMWEB_SAVE_SESSIONS=0 이면 끔
sessions/submissions/ "여기서 멈춤" 기록 (JSON + 손 스냅샷 jpg). 항상 저장한다

## 손 모델 다시 만들기

```bash
uv run --group tools python tools/prepare_hand.py
```

원본 `assets/hand/generic-hand-right.glb`에서 `web/public/models/hand.glb`와 `hand.joints.json`을 다시 만든다.
```

## 통신 (/ws/track)

- 브라우저 → 서버: JPEG 프레임(바이너리, 최대 1280px), 또는 `{"type": "reset" | "capture_now" | "ping"}`
- 서버 → 브라우저: 프레임마다 `track` (랜드마크, 안내 문구, 게이지), 캡처 시 `capturing` → `capture` (손금 PalmLineSet JSON), 실패 시 `error`
- 캡처 뒤 이어서: `interp_start` (묘사 문장, 단계 설정) → `interp` 단계마다 도착 순서대로 → `interp_done`. 해석 불가 시 `interp_error`

## 해석 설정 (환경 변수)

| 변수 | 기본값 | 뜻 |
| --- | --- | --- |
| `PALMWEB_MODEL` | `qwen/qwen3.5-9b` | OpenRouter 모델 |
| `PALMWEB_PROVIDER` | `DeepInfra` | 무너지는 구간(7단계~)에 쓸 업체 |
| `PALMWEB_FALLBACK_MAX_STEP` | `6` | 이 단계까지는 가장 빠른 업체를 쓴다 |

단계별 temperature·min_p와 시스템 프롬프트는 `server/llm.py`의 `STEPS`, `SYSTEM_PROMPT`에서 고친다.

## 점검

```bash
uv run python -m server.smoke_test ../palmlines/samples/hand70.jpg
```

자동 캡처 후 해석 10단계가 도착하는 시간과 첫 글을 출력한다.

### 리허설 부하 시험

전시 서버와 섞이지 않게 다른 포트에 시험용 서버를 띄우고 돌린다. 실제 OpenRouter를 호출한다 (30분에 약 0.02달러).

```bash
PALMWEB_SAVE_SESSIONS=0 uv run uvicorn server.main:app --port 8012
```

```bash
uv run python -m server.soak_test --minutes 30 --url ws://localhost:8012
```

결과는 `logs/soak_<시각>.json`. 캡처·해석 지연, 실패 단계, 노브-화면 단계 어긋남, 서버 메모리를 기록한다.
