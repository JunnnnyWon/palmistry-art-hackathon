"""손금 정답 라벨링 도구. 정면화된 손바닥 위에 주요 선을 클릭으로 그린다.

    uv run python label_tool.py tests/testset            # 앱에서 c로 저장한 케이스들
    uv run python label_tool.py "samples/*.jpg"

조작
  1 감정선 · 2 두뇌선 · 3 생명선 · 4 운명선   (그릴 선 종류 선택)
  왼쪽 클릭: 점 추가 (선을 따라 5~15개 정도)
  enter 또는 오른쪽 클릭: 현재 선 마치기
  u: 마지막 점 취소 · x: 마지막 선 삭제 · p: 자동 추출 결과 보기/숨기기
  s: 저장 · n: 저장하고 다음 · q: 종료
"""
from __future__ import annotations

import argparse

import cv2
import numpy as np

from palmlines.hand import HandTracker
from palmlines.model import LINE_KINDS
from palmlines.render import Style
from palmlines.testset import detect_all, expand_cases, load_frames, load_label, median_roi, save_label
from palmlines.ui import TextDrawer

VIEW = 1024
KEYS = {ord("1"): "heart", ord("2"): "head", ord("3"): "life", ord("4"): "fate"}
NAMES = {"heart": "감정선", "head": "두뇌선", "life": "생명선", "fate": "운명선"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--hint-method", default="merged", help="p 키로 보여줄 자동 추출 방식")
    args = ap.parse_args()
    cases = expand_cases(args.cases)
    if not cases:
        raise SystemExit("케이스가 없습니다")
    style, text = Style(), TextDrawer()
    win = "label"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    state = {"cur": [], "kind": "heart"}

    def on_mouse(ev, x, y, flags, _):
        if ev == cv2.EVENT_LBUTTONDOWN:
            state["cur"].append((x / VIEW, y / VIEW))
        elif ev == cv2.EVENT_RBUTTONDOWN:
            state["finish"] = True

    cv2.setMouseCallback(win, on_mouse)
    with HandTracker(mode="image") as tracker:
        for ci, case in enumerate(cases):
            pairs = detect_all(tracker, load_frames(case))
            if not pairs:
                print(f"{case}: 손 미검출, 건너뜀")
                continue
            roi, lm = median_roi(pairs)
            base = cv2.resize(roi, (VIEW, VIEW), interpolation=cv2.INTER_CUBIC)
            lab = load_label(case)
            lines = lab["lines"] if lab else []
            hint, show_hint = None, False
            state.update(cur=[], finish=False)
            while True:
                if state.get("finish"):
                    if len(state["cur"]) >= 2:
                        lines.append({"kind": state["kind"], "points": [list(p) for p in state["cur"]]})
                    state.update(cur=[], finish=False)
                vis = base.copy()
                if show_hint and hint is not None:
                    for l in hint.lines:
                        p = np.round(l.points * VIEW).astype(np.int32)
                        cv2.polylines(vis, [p], False, (200, 200, 200), 1, cv2.LINE_AA)
                for l in lines:
                    p = np.round(np.array(l["points"]) * VIEW).astype(np.int32)
                    cv2.polylines(vis, [p], False, style.line_colors[l["kind"]], 3, cv2.LINE_AA)
                if state["cur"]:
                    p = np.round(np.array(state["cur"]) * VIEW).astype(np.int32)
                    cv2.polylines(vis, [p], False, style.line_colors[state["kind"]], 2, cv2.LINE_AA)
                    for q in p:
                        cv2.circle(vis, tuple(q), 4, (255, 255, 255), -1)
                done = ", ".join(NAMES.get(l["kind"], l["kind"]) for l in lines) or "없음"
                text(vis, f"[{ci + 1}/{len(cases)}] {case.name}   그리는 선: {NAMES[state['kind']]}", (12, 10), 26)
                text(vis, f"완료: {done}   (1~4 종류 · 클릭 점 · enter 마침 · u/x 취소 · p 힌트 · n 다음)", (12, 44), 20)
                cv2.imshow(win, vis)
                k = cv2.waitKey(20) & 0xFF
                if k in KEYS:
                    state["kind"] = KEYS[k]
                elif k in (13, 10):
                    state["finish"] = True
                elif k == ord("u") and state["cur"]:
                    state["cur"].pop()
                elif k == ord("x") and lines:
                    lines.pop()
                elif k == ord("p"):
                    if hint is None:
                        from palmlines.pipeline import extract_lines
                        hint, _ = extract_lines(pairs, method=args.hint_method)
                    show_hint = not show_hint
                elif k in (ord("s"), ord("n")):
                    save_label(case, lines, lm)
                    print(f"[save] {case}: {len(lines)}선")
                    if k == ord("n"):
                        break
                elif k == ord("q"):
                    cv2.destroyAllWindows()
                    return
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
