"""사진 파일로 파이프라인 확인: 단계별 디버그 패널과 최종 렌더를 out/ 에 저장한다.

    uv run python run_image.py samples/*.jpg
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

import cv2
import numpy as np

from palmlines.hand import HandTracker
from palmlines.pipeline import METHODS, lines_from_frame
from palmlines.render import Style, render


def _tile(img, size=320, label=""):
    if img.dtype == bool:
        img = img.astype(np.uint8) * 255
    if img.dtype != np.uint8:
        img = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    cv2.putText(img, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
    return img


def overlay(roi, ls, style):
    vis = roi.copy()
    s = roi.shape[0]
    for l in ls.lines:
        p = np.round(l.points * s).astype(np.int32)
        cv2.polylines(vis, [p], False, style.line_colors[l.kind], 2, cv2.LINE_AA)
    return vis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out", default="out")
    ap.add_argument("--method", default="merged", choices=METHODS)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    files = [f for pat in args.images for f in sorted(glob.glob(pat))]
    style = Style()
    with HandTracker(mode="image") as tracker:
        for f in files:
            img = cv2.imread(f)
            if img is None:
                print(f"{f}: 읽기 실패")
                continue
            s = 1600 / max(img.shape[:2])
            if s < 1:
                img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            obs = tracker.detect(img)
            if obs is None:
                print(f"{f}: 손 미검출")
                continue
            ls, dbg = lines_from_frame(img, obs, method=args.method)
            roi = dbg["roi"]
            masked = cv2.bitwise_and(roi, roi, mask=dbg["mask"])
            first = dbg.get("enhanced")
            if first is None:
                first = cv2.cvtColor(dbg["net_input"], cv2.COLOR_RGB2BGR)
            panel = np.hstack([
                _tile(masked, label="ROI+mask"),
                _tile(first, label="enhanced" if dbg.get("enhanced") is not None else "net input"),
                _tile(dbg["ridge"], label="ridge/prob"),
                _tile(dbg["skeleton"], label="skeleton"),
                _tile(overlay(roi, ls, style), label="lines"),
                _tile(render(ls, 640, style), label="render"),
            ])
            stem = f"{Path(f).stem}_{args.method}"
            cv2.imwrite(str(out / f"debug_{stem}.jpg"), panel)
            cv2.imwrite(str(out / f"render_{stem}.png"), render(ls, 800, style))
            kinds = ", ".join(f"{l.kind}({l.length:.2f},c{l.confidence:.2f})" for l in ls.lines)
            print(f"{Path(f).name}: palm={obs.palm_facing} {dbg['ms']:.0f}ms {len(ls.lines)}선 → {kinds}")


if __name__ == "__main__":
    main()
