#!/bin/zsh
# OpenRouter temperature 본 실험 (2026-11-06). 모델별로 병렬, 모델 안에서는 조건 A→B→C 순서.
cd "$(dirname "$0")/../.."
T=(0.1 0.3 0.5 0.7 0.9 1.1 1.3 1.5 1.7 1.9)
SYS="한 문장으로 정의"; Q="죽음에 대해 어떻게 생각해?"; O=results/openrouter
run() { python3 openrouter_temperature_test.py --temps $T --runs 10 --system "$SYS" --prompt "$Q" --summary "$@"; }
(
  run --model qwen/qwen3.5-9b --provider DeepInfra --no-reasoning --out $O/qwen35-9b_A.json
  run --model qwen/qwen3.5-9b --provider DeepInfra --no-reasoning --top-p 1 --top-k 0 --min-p 0 --out $O/qwen35-9b_B.json
  run --model qwen/qwen3.5-9b --provider DeepInfra --no-reasoning --top-p 1 --top-k 0 --min-p 0.1 --out $O/qwen35-9b_C.json
) > $O/log_qwen.txt 2>&1 &
(
  run --model google/gemma-4-31b-it --provider DeepInfra --out $O/gemma4-31b_A.json
  run --model google/gemma-4-31b-it --provider DeepInfra --top-p 1 --top-k 0 --min-p 0 --out $O/gemma4-31b_B.json
  run --model google/gemma-4-31b-it --provider DeepInfra --top-p 1 --top-k 0 --min-p 0.1 --out $O/gemma4-31b_C.json
) > $O/log_gemma.txt 2>&1 &
(
  run --model google/gemini-3.5-flash-lite --provider "Google AI Studio" --out $O/gemini35-flash-lite_A.json
) > $O/log_gemini.txt 2>&1 &
wait
echo ALL_DONE
