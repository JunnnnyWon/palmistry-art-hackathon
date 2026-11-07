# 손금 노브 (라즈베리파이 터치스크린)

전시 옆 라즈베리파이 4 + 7인치 터치스크린(800×480)에서 해석 단계 1~10을 돌리는 노브.
맥 서버의 `/ws/control`에 Wi-Fi로 붙는다. 단계 값은 서버가 하나로 들고 있어서, 노브와 전시 화면 방향키 어느 쪽으로 바꿔도 양쪽이 같이 움직인다.

## 전시 번호별 짝: /r1 ↔ /n1

여러 전시 화면이 각자 노브를 갖는다. 전시 화면과 노브가 같은 번호면 같은 방에 들어가 단계를 공유한다.

| 주소 | 열리는 것 |
| --- | --- |
| `http://<서버>/r1` | 전시 화면, r1 방 (화면의 단계 버튼은 숨김) |
| `http://<서버>/n1` | 노브, r1 방을 조종 (아이패드·휴대폰 브라우저) |
| `http://<서버>/r2`, `/n2` … | 같은 방식으로 r2, r3 … |
| `http://<서버>/` | 일반 관객. 접속마다 개인 방, 노브 없이 화면에서 조작 |
| `http://<서버>/knob`, 라즈베리파이 앱 | 전시장(exhibit) 방. 전시 실행 스크립트의 기본 화면(`?room=exhibit`)과 짝 |

- `/r1?src=/samples/hand70.jpg`처럼 다른 옵션을 붙이면 이어서 전달된다 (손 없이 시험할 때).
- 노브는 `?room=이름`으로 아무 방이나 지정할 수도 있다.
- 서버가 HTTPS 주소면 노브는 자동으로 보안 연결(wss)로 붙는다.
- 아이패드에서는 Safari로 `/n1`을 연 뒤 공유 → "홈 화면에 추가"를 하면 전체 화면 앱처럼 쓸 수 있다.
- 공개 서버: `https://hackathon.junnnny.kr/r1` ↔ `https://hackathon.junnnny.kr/n1`. 노브 파일을 고치면 GPU 서버에도 올려야 한다 (요청마다 파일을 읽으므로 재시작 불필요):

```bash
rsync -a -e "ssh -o BatchMode=yes" "/Users/junnnny/Desktop/예술해커톤/palmweb/knob/index.html" junnnny@100.90.117.108:palmistry-art/palmweb/knob/index.html
```

## 바로 써 보기 (어느 기기든)

같은 Wi-Fi의 휴대폰·태블릿 브라우저에서 연다.

```
http://<맥 IP>:8000/knob
```

맥 IP는 전시 실행 스크립트가 시작할 때 출력한다. 2026-11-06 기준 `10.10.100.41`.
처음 접속하면 macOS가 "들어오는 연결을 허용할까요?"를 물을 수 있다. 허용해야 노브가 붙는다.

## 라즈베리파이에 설치

이 라즈베리파이는 Tizen 6.0 IoT(headed)다 (IP `10.10.101.193`, sdb 포트 26101). 노브는 Tizen 웹 앱으로 설치한다.
Tizen은 서명된 앱만 설치하므로 처음 한 번 서명 준비가 필요하다.

### 서명 준비 (처음 한 번)

> 2026-11-06에 이 맥(전시용)에서 아래를 이미 마쳤다. Tizen Studio CLI 6.1(`tizen` 2.5.25)이 `~/tizen-studio`에,
> 작성자 인증서가 `~/tizen-studio-data/keystore/author/palm-author.p12`에 있고, 보안 프로필 이름은 `palm`이다.
> 인증서 비밀번호는 같은 폴더의 `palm-author.password`(본인만 읽기)에 있다. 다른 맥에서 할 때만 아래를 따른다.

1. Tizen Studio CLI를 설치한다. 공식 배포 서버의 `web-cli_Tizen_Studio_6.1_macos-64.bin`(약 280MB)을 받아 `bash <파일> --accept-license ~/tizen-studio`로 설치한다. Apple Silicon에서는 Rosetta가 필요하다.
2. 작성자 인증서와 보안 프로필을 만든다. 배포자 인증서는 Tizen 기본값을 쓴다 (삼성 TV용 인증서 아님).

```bash
~/tizen-studio/tools/ide/bin/tizen certificate -a palm -p <비밀번호> -f palm-author
```

```bash
~/tizen-studio/tools/ide/bin/tizen security-profiles add -n palm -a ~/tizen-studio-data/keystore/author/palm-author.p12 -p <비밀번호>
```

```bash
~/tizen-studio/tools/ide/bin/tizen cli-config "profiles.path=$HOME/tizen-studio-data/profile/profiles.xml"
```

### 설치·실행

```bash
cd palmweb && uv run python tools/build_knob_wgt.py --install
```

맥의 현재 IP를 기본 서버 주소로 넣어 묶고 → 서명하고 → 라즈베리파이에 설치하고 → 실행한다.
서명 없이 `sdb install` 하면 `Check certificate error [-12]`로 거부된다 (2026-11-06 확인).

라즈베리파이를 껐다 켜면 노브 앱은 자동으로 뜨지 않는다. 다시 실행만 하려면:

```bash
~/tizen-sdb/sdb -s 10.10.101.193:26101 shell "app_launcher -s PalmKnob01.knob"
```

## 화면

검정 배경 위 밝은 아이보리색 입체 노브 하나만 화면 높이를 거의 다 채운다. 글자·숫자는 없다.

- 노브: 고정 조명을 받은 돔 형태 윗면, 그림자, 테두리의 손잡이 홈(널링), 파인 홈 안의 단계 색 지시선. 노브가 돌아도 반사광은 제자리.
- LED 링: 노브 둘레 10개. 현재 단계까지 단계 색으로 켜진다. 꺼진 LED 중 희미하게 빛나는 것은 해석 글이 이미 도착한 단계.
- 색: 1~6단계 초록~노랑(해석이 유지됨), 7단계부터 주황~빨강(말이 흔들리고 무너지는 구간).
- 단계별 반응 (`index.html`의 `REACT` 표에서 세기를 조절):

| 단계 | 반응 |
| --- | --- |
| 1~3 | 고요함. 넘어갈 때 딸깍 눌림만 |
| 4~6 | 노브 뒤 빛이 숨 쉬듯 퍼졌다 줄어듦 |
| 7 | 가끔 미세하게 떨림. 넘어갈 때 튕김 |
| 8 | 떨림이 잦아지고 LED가 깜빡임. 표면에 옅은 균열 |
| 9 | 노브가 흔들리고 균열이 뚜렷해짐. 붉은 맥박 시작 |
| 10 | 강하게 요동, 붉은 빛이 맥박처럼 뜀, 균열이 완전히 드러남 |

- 조작: 화면 어디를 문질러도 노브 중심 기준 각도로 돌아가고, 10칸 중 가장 가까운 칸에 맞춰진다. 손가락을 대고 있으면 노브가 살짝 눌린다.
- 상태는 밝기로만: 손금을 찍기 전이면 노브 전체가 어둡고(돌려도 안 바뀜), 서버 연결이 끊기면 더 어두워지며 왼쪽 위에 작은 빨간 점이 뜬다.
- 서버 주소 바꾸기: 왼쪽 위 구석을 2초 길게 누른다.
- 점검용 주소 옵션: `?server=<IP:포트>`, `?demo=7`(서버 없이 7단계 체험 중으로 표시), `?noanim=1`(회전 없이 바로 이동).
- 라즈베리파이 성능을 위해 SVG 흐림 필터를 쓰지 않고, 미리 그린 그라디언트의 투명도·위치만 바꾼다.

## 통신 (/ws/control)

- 노브 → 서버: `{"type": "set_step", "step": 7}`, `{"type": "delta", "delta": 1}`, `{"type": "ping"}`
- 서버 → 노브·전시 화면: `{"type": "step", "step", "phase", "temperature", "min_p", "ready", "source", "displays", "knobs"}`
- 체험 중(`phase: result`)이 아니면 바꾸지 않고 `source: "rejected"`로 현재 상태를 돌려준다.

## 호환성

Tizen 6.0 웹 엔진은 오래된 Chromium 기반이라 `index.html`은 `?.`, `??`, 클래스 필드, `min()`, `aspect-ratio`, `inset`, flex `gap`을 쓰지 않는다.
