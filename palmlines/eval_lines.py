"""손금 추출 방식 비교 평가 (명세서 5.3절).

    uv run python eval_lines.py tests/testset "samples/*.jpg"
    uv run python eval_lines.py tests/testset --methods unet merged --report out/eval_report.md

정답 라벨이 있는 케이스: 커버리지 · 오검출 · 분절 수 · 분류 정확도 · 처리 시간
라벨이 없는 케이스   : 주요 선 검출 개수 · 전체 선 수 · 처리 시간 (라벨 없이 볼 수 있는 지표만)

지표 정의 (512px 표준 ROI 기준)
- 커버리지: 정답 선을 2px 간격으로 샘플링한 점 중 예측 선에서 tol px 안에 있는 비율. 목표 ≥ 0.8
- 오검출  : 예측 주요 선(기타 제외) 길이 중 어느 정답 선에서도 2·tol px 넘게 떨어진 비율. 목표 ≤ 0.3
- 분절 수 : 정답 선 하나를 덮는(점의 15% 이상) 예측 선 개수. 목표 ≤ 2
- 분류    : 정답 선을 덮는 예측 점들의 다수 종류가 정답 종류와 같은가. 목표 ≥ 0.8
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

from palmlines.hand import HandTracker
from palmlines.pipeline import METHODS, extract_lines, get_unet
from palmlines.testset import detect_all, expand_cases, load_frames, load_label

SIZE = 512
PRINCIPAL = ("heart", "head", "life", "fate")


def _resample(pts: np.ndarray, step: float) -> np.ndarray:
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    if s[-1] < step:
        return pts
    t = np.arange(0, s[-1], step)
    return np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], axis=1)


def _raster(polys: list[np.ndarray]) -> np.ndarray:
    img = np.zeros((SIZE, SIZE), np.uint8)
    for p in polys:
        cv2.polylines(img, [np.round(p).astype(np.int32)], False, 255, 1, cv2.LINE_8)
    return img


def score_labeled(pred_lines, label: dict, tol: float) -> dict:
    gt = [(l["kind"], _resample(np.asarray(l["points"], np.float64) * SIZE, 2.0)) for l in label["lines"]]
    pred = [(l.kind, _resample(l.points.astype(np.float64) * SIZE, 2.0)) for l in pred_lines]
    if not gt:
        return {}
    # 예측 선까지 거리
    pred_r = _raster([p for _, p in pred])
    d_pred = cv2.distanceTransform(255 - pred_r, cv2.DIST_L2, 5) if pred else np.full((SIZE, SIZE), 1e9, np.float32)
    gt_r = _raster([p for _, p in gt])
    d_gt = cv2.distanceTransform(255 - gt_r, cv2.DIST_L2, 5)

    def dist(dmap, pts):
        q = np.clip(np.round(pts).astype(int), 0, SIZE - 1)
        return dmap[q[:, 1], q[:, 0]]

    cover_n = cover_d = 0
    frags, correct = [], []
    for kind, g in gt:
        dg = dist(d_pred, g)
        cover_n += int((dg <= tol).sum())
        cover_d += len(g)
        # 이 정답 선의 각 점에 가장 가까운 예측 선
        owners = []
        for gp in g:
            best, bd = None, tol
            for i, (_, p) in enumerate(pred):
                d = float(np.min(np.linalg.norm(p - gp, axis=1)))
                if d <= bd:
                    best, bd = i, d
            if best is not None:
                owners.append(best)
        cnt = Counter(owners)
        nf = sum(1 for c in cnt.values() if c >= 0.15 * len(g))
        if nf:   # 아예 놓친 선은 분절이 아니라 커버리지에서 벌점
            frags.append(nf)
        if kind in PRINCIPAL:
            kinds = Counter(pred[i][0] for i in owners)
            correct.append(bool(kinds) and kinds.most_common(1)[0][0] == kind)
    fp_n = fp_d = 0
    for kind, p in pred:
        if kind == "minor":
            continue
        dp = dist(d_gt, p)
        fp_n += int((dp > 2 * tol).sum())
        fp_d += len(p)
    return {
        "coverage": cover_n / max(cover_d, 1),
        "false_pos": fp_n / max(fp_d, 1),
        "fragments": float(np.mean(frags)) if frags else float("nan"),
        "cls_acc": float(np.mean(correct)) if correct else float("nan"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--methods", nargs="+", default=list(METHODS), choices=METHODS)
    ap.add_argument("--tol", type=float, default=6.0, help="허용 거리(px, 512 ROI 기준)")
    ap.add_argument("--report", default="out/eval_report.md")
    args = ap.parse_args()
    cases = expand_cases(args.cases)
    if not cases:
        raise SystemExit("케이스가 없습니다")
    if any(m != "classical" for m in args.methods):
        get_unet()

    per_case = []
    agg = defaultdict(lambda: defaultdict(list))
    with HandTracker(mode="image") as tracker:
        for case in cases:
            pairs = detect_all(tracker, load_frames(case))
            label = load_label(case)
            if not pairs:
                print(f"{case.name}: 손 미검출")
                continue
            for m in args.methods:
                ls, dbg = extract_lines(pairs, method=m, keep_roi=False)
                kinds = {l.kind for l in ls.lines}
                row = {"case": case.name, "method": m, "labeled": label is not None, "ms": dbg["ms"],
                       "n_lines": len(ls.lines), "n_major3": sum(k in kinds for k in ("heart", "head", "life"))}
                if label:
                    if label.get("landmarks") is not None:
                        drift = float(np.abs(np.asarray(label["landmarks"]) - ls.landmarks).max() * SIZE)
                        if drift > 3:
                            print(f"  주의: {case.name} 랜드마크가 라벨 시점과 {drift:.1f}px 다름")
                    row.update(score_labeled(ls.lines, label, args.tol))
                per_case.append(row)
                for k, v in row.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        agg[(m, row["labeled"])][k].append(v)

    lines = ["# 손금 추출 방식 평가", "", f"- 케이스: {len(cases)}개 (라벨 있음 {sum(load_label(c) is not None for c in cases)}개)",
             f"- 허용 거리: {args.tol}px (512 ROI 기준)", ""]
    has_lab = any(r["labeled"] for r in per_case)
    if has_lab:
        lines += ["## 라벨 있는 케이스 평균", "",
                  "| 방식 | 커버리지 ↑ | 오검출 ↓ | 분절 수 ↓ | 분류 정확도 ↑ | 처리 ms |", "| --- | --- | --- | --- | --- | --- |"]
        for m in args.methods:
            a = agg.get((m, True))
            if a:
                f = lambda k: np.nanmean(a[k]) if a.get(k) else float("nan")
                lines.append(f"| {m} | {f('coverage'):.2f} | {f('false_pos'):.2f} | {f('fragments'):.2f} | "
                             f"{f('cls_acc'):.2f} | {f('ms'):.0f} |")
        lines.append("")
    lines += ["## 전체 케이스 (라벨 없이 볼 수 있는 지표)", "",
              "| 방식 | 주요 3선 검출 (평균/3) | 3선 모두 검출 케이스 | 전체 선 수 | 처리 ms |", "| --- | --- | --- | --- | --- |"]
    for m in args.methods:
        rows = [r for r in per_case if r["method"] == m]
        if rows:
            n3 = np.mean([r["n_major3"] for r in rows])
            full = sum(r["n_major3"] == 3 for r in rows)
            lines.append(f"| {m} | {n3:.2f} | {full}/{len(rows)} | {np.mean([r['n_lines'] for r in rows]):.1f} | "
                         f"{np.mean([r['ms'] for r in rows]):.0f} |")
    lines += ["", "## 케이스별", "", "| 케이스 | 방식 | 주요 3선 | 선 수 | 커버리지 | 오검출 | 분절 | 분류 | ms |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    fmt = lambda r, k: f"{r[k]:.2f}" if k in r else "-"
    for r in per_case:
        lines.append(f"| {r['case']} | {r['method']} | {r['n_major3']}/3 | {r['n_lines']} | {fmt(r, 'coverage')} | "
                     f"{fmt(r, 'false_pos')} | {fmt(r, 'fragments')} | {fmt(r, 'cls_acc')} | {r['ms']:.0f} |")
    report = "\n".join(lines) + "\n"
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(report)
    print(report)


if __name__ == "__main__":
    main()
