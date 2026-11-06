# palmlines

웹캠으로 손바닥을 인식해 손금을 추출하고, 단순한 손 아웃라인 위에 손금을 그린다.
설계는 상위 폴더의 `손금_프로젝트_개발명세서.md` 참고. 현재 M2(U-Net 통합 + 평가 도구)까지 구현.

## 준비

```bash
cd palmlines
uv sync
```

모델 파일은 git에 넣지 않는다. 처음 한 번 받는다.

```bash
mkdir -p assets/checkpoints && curl -L -o assets/hand_landmarker.task https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task && curl -L -o assets/checkpoints/yeonsumia_unet_aug_epoch70.pth https://github.com/yeonsumia/palmistry/raw/HEAD/code/checkpoint/checkpoint_aug_epoch70.pth
```

MediaPipe 손 랜드마크 모델(7.8MB)과 [yeonsumia/palmistry](https://github.com/yeonsumia/palmistry)의 U-Net 가중치(55MB, Apache-2.0)다.
웹캠 테스트 세트(`tests/testset/`)는 얼굴이 찍혀 있어 저장소에 넣지 않는다.

가중치나 torch가 없으면 앱은 고전 CV 방식으로 자동 전환한다.

## 실행

```bash
uv run python app.py               # 저장된 카메라 (처음엔 0번)
uv run python app.py --pick-camera # 카메라별 미리보기를 보고 숫자 키로 골라 저장
uv run python app.py --camera 1    # 이번만 특정 번호
```

Finder에서 `웹캠_실행.command`를 더블클릭해도 된다. 카메라 권한은 macOS 터미널 앱에 주어지므로,
Claude 앱 안의 터미널 패널에서는 카메라가 열리지 않는다.

카메라 번호 순서는 OpenCV와 ffmpeg·시스템 정보가 서로 다르다. 이 맥에서는 OpenCV 기준
0번이 iPhone 연속성 카메라, 1번이 MacBook Pro 카메라였다 (2026-11-06 확인, `.camera`에 1 저장).
iPhone을 가까이 두거나 치우면 순서가 바뀔 수 있으니 이상하면 `--pick-camera`로 다시 고른다.

처음 실행하면 macOS가 터미널 앱의 카메라 접근을 묻는다. 거부했다면
시스템 설정 > 개인정보 보호 및 보안 > 카메라에서 사용하는 터미널 앱을 켠다.

손바닥을 카메라 20~30cm 앞에 펴서 보여주고 1초쯤 멈추면 자동 캡처된다.
손이 너무 가깝거나(손목~중지 뿌리가 화면 높이의 50% 초과) 손끝·손목이 화면 밖으로 잘리면
캡처하지 않고 "손을 조금 더 멀리" 또는 "손 전체가 화면 안에 들어오게" 안내가 뜬다.

## 손 윤곽과 손금 경계

손 윤곽 하나(`palmlines/shape.py`)를 손금 추출 영역, 렌더 윤곽, 선 잘라내기에 함께 쓴다.
그래서 그려진 손금은 항상 그려진 손 윤곽 안에 있다.

- `traced` (기본): 랜드마크 기본 윤곽을 실제 영상의 손 경계에 맞춰 다듬는다 (GrabCut).
  배경이 손에 붙지 않도록 기본 윤곽 주변 좁은 띠 안에서만 움직인다.
- `simple`: 랜드마크만으로 만든 매끄러운 단순 윤곽.

윤곽은 손금 벡터 JSON의 `outline`에 표준 좌표로 저장된다. 다음 단계(손금 확장)의 경계로 쓴다.

| 키 | 동작 |
| --- | --- |
| space | 지금 바로 캡처 |
| r | 다시 (추적 상태로) |
| s | 결과 저장: `captures/palm_*.png` + 손금 벡터 `palm_*.json` |
| m | 추출 방식 전환 (merged → classical → unet), 같은 캡처를 다시 추출 |
| o | 손 윤곽 전환 (traced 실제 경계 ↔ simple 단순 윤곽), 같은 캡처를 다시 추출 |
| c | 방금 캡처를 평가용 테스트 세트에 저장 (`tests/testset/<시각>/frame_*.png`) |
| d | 디버그 패널 |
| f | 전체화면 전환 |
| q, esc | 종료 |

## 추출 방식

| 방식 | 내용 | 특징 |
| --- | --- | --- |
| `merged` (기본) | 주요 선은 U-Net, U-Net 선과 겹치지 않는 고전 CV 선은 기타 잔선으로 추가 | 주요 선이 깨끗하고, 확장 단계에서 쓸 잔선도 확보 |
| `unet` | [yeonsumia/palmistry](https://github.com/yeonsumia/palmistry) U-Net만 | 가장 깨끗함. 주요 선 약 3개만 나옴 |
| `classical` | 릿지 필터 기반 고전 CV | 모델 불필요. 잔주름 잡음이 많고 가끔 주요 선을 놓침 |

선 색: 감정선 빨강, 두뇌선 파랑, 생명선 초록, 운명선 노랑, 기타 회색.

## 테스트 세트와 평가

1. 앱에서 캡처 후 `c`로 테스트 케이스를 모은다 (사람 × 양손 × 조명 × 거리).
2. 정답 선을 그린다.

```bash
uv run python label_tool.py tests/testset
```

`1`~`4`로 선 종류(감정·두뇌·생명·운명)를 고르고, 선을 따라 클릭한 뒤 enter로 마친다. `p`는 자동 추출 결과를 힌트로 겹쳐 보여주고, `n`은 저장하고 다음 케이스로 넘어간다.

3. 세 방식을 비교한다.

```bash
uv run python eval_lines.py tests/testset --report out/eval_report.md
```

사진 파일도 케이스로 쓸 수 있다 (`"samples/*.jpg"`). 라벨은 `<이름>.label.json`으로 옆에 저장된다.

## 사진으로 점검

```bash
uv run python run_image.py "samples/*.jpg" --method merged   # out/debug_*.jpg, out/render_*.png
uv run python app.py --source samples/hand70.jpg --headless --frames 40   # 앱 흐름 점검
```

`samples/`는 yeonsumia/palmistry(Apache-2.0)의 예시 손바닥 사진이다.

## 구조

```
palmlines/
  hand.py                MediaPipe 손 랜드마크, 손바닥 정면 판정, 정지 게이트
  rectify.py             표준 손바닥 좌표계로 정면화(닮음변환), 손바닥 마스크
  extract/classical.py   조명 평탄화 → CLAHE → Sato 릿지 → 히스테리시스 이진화 → 스켈레톤
  extract/unet.py        U-Net 추론 (원본 학습 입력 기하 재현, 여러 프레임 로짓 평균)
  extract/unet_model.py  U-Net + Context Fusion 구조 (yeonsumia, Apache-2.0)
  extract/postprocess.py 스켈레톤 → 최장 경로 분해 → 꺾임 분할 → 조각 병합 → 스무딩
  extract/classify.py    끝점·위치 규칙으로 감정/두뇌/생명/운명선 분류
  pipeline.py            extract_lines(frames, method) 진입점
  shape.py               손 윤곽(해부학적 기본 윤곽, 실제 경계 보정, 윤곽 밖 선 잘라내기)
  render.py              손 윤곽 + 손금 렌더
  model.py               Polyline, PalmLineSet, JSON 저장/불러오기
  testset.py, ui.py      테스트 케이스 입출력, 한글 텍스트
app.py                   웹캠 실시간 앱
label_tool.py            정답 라벨링
eval_lines.py            방식 비교 평가
run_image.py             사진 일괄 점검
```
