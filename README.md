# 예술 해커톤: 손금 × AI

관객의 손금을 웹캠으로 인식하고, AI(Qwen)가 그 손금을 해석한다. 관객이 다이얼(temperature)을 올리면 손금에서 뿌리가 손 전체로 퍼지고 AI의 말도 흔들리다 무너진다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [손금_웹체험_개발명세서.md](손금_웹체험_개발명세서.md) | 웹 체험 전체 설계 (3D 손, AI 해석, 10단계 확산). 현재 기준 문서 |
| [손금_프로젝트_개발명세서.md](손금_프로젝트_개발명세서.md) | 손금 인식 파이프라인 설계와 구현 기록 |
| [temperature_실험_인수인계.md](temperature_실험_인수인계.md) | Gemini temperature 실험 |
| [temperature_실험_OpenRouter.md](temperature_실험_OpenRouter.md) | OpenRouter 모델 비교 실험, Qwen3.5-9B 선정 근거 |
| [오후회의록_정리본_2026-11-05.md](오후회의록_정리본_2026-11-05.md) | 11/5 아이디어 회의 정리 |

## 폴더

| 폴더·파일 | 내용 |
| --- | --- |
| [palmlines/](palmlines/) | 손금 인식 앱 (MediaPipe + 고전 CV + U-Net). 실행법은 그 안의 README |
| `gemini_temperature_test.py`, `openrouter_temperature_test.py` | temperature 실험 스크립트 |
| [results/openrouter/](results/openrouter/) | OpenRouter 실험 원자료와 Qwen 답변 보기 페이지 |
| [servo/](servo/) | Raspberry Pi 서보 테스트 코드 |

## 준비

`.env.example`을 `.env`로 복사하고 API 키를 채운다. `.env`는 저장소에 올라가지 않는다.
