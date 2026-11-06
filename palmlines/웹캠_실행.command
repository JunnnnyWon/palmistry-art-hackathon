#!/bin/zsh
# Finder에서 더블클릭하면 macOS 터미널 앱에서 손금 앱을 실행한다.
# 처음 실행할 때 '터미널'이 카메라에 접근하려 한다는 창이 뜨면 허용.
# 실행 기록은 out/last_run.log 에도 남는다 (문제 확인용).
cd "$(dirname "$0")"
mkdir -p out
PYTHONUNBUFFERED=1 .venv/bin/python app.py "$@" 2>&1 | tee out/last_run.log
echo
read "?엔터를 누르면 창을 닫습니다..."
