"""손 랜드마크 기준 위치 규칙으로 선 종류를 정한다 (표준 좌표계: 엄지 왼쪽, 손가락 위).

- 감정선(heart): 손가락 뿌리 바로 아래 띠, 가로, 소지 쪽에 치우침
- 두뇌선(head) : 감정선 아래 띠, 가로~비스듬, 손바닥 가운데
- 생명선(life) : 엄지 두덩이를 감싸는 곡선, 엄지 쪽, 세로 성분 큼
- 운명선(fate) : 손바닥 가운데를 세로로 지나는 선
"""
from __future__ import annotations

import numpy as np

from ..hand import INDEX_MCP, MIDDLE_MCP, PINKY_MCP, RING_MCP, THUMB_MCP, WRIST
from ..model import Polyline


def _g(x: float, mu: float, sd: float) -> float:
    return float(np.exp(-0.5 * ((x - mu) / sd) ** 2))


def _frame(lm: np.ndarray):
    """손바닥 상대 좌표: x는 검지뿌리=0, 소지뿌리=1 / y는 손가락뿌리 라인=0, 손목=1."""
    mcp_y = float(lm[[INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP], 1].mean())
    H = float(lm[WRIST, 1] - mcp_y)
    x0, x1 = float(lm[INDEX_MCP, 0]), float(lm[PINKY_MCP, 0])
    W = max(x1 - x0, 1e-6)

    def rel(p):
        p = np.atleast_2d(p)
        return np.stack([(p[:, 0] - x0) / W, (p[:, 1] - mcp_y) / H], axis=1)
    return rel, W / H


def _features(pl: Polyline, lm: np.ndarray) -> dict:
    rel, aspect = _frame(lm)
    pts = rel(pl.points)
    pts_iso = pts * np.array([aspect, 1.0])            # 각도·거리 계산용 등방 좌표
    # 엄지-검지 사이 틈: 생명선·두뇌선이 시작하는 곳
    gap = rel((lm[THUMB_MCP] + lm[INDEX_MCP]) / 2)[0]
    gap = gap + np.array([0.08, 0.08])                  # 틈보다 살짝 손바닥 안쪽
    c = pts.mean(axis=0)
    cov = np.cov((pts_iso - pts_iso.mean(0)).T) if len(pts) > 2 else np.eye(2)
    _, evecs = np.linalg.eigh(cov)
    d = evecs[:, -1]
    theta = float(np.degrees(np.arctan2(abs(d[1]), abs(d[0]))))   # 0=가로, 90=세로
    ends = pts[[0, -1]]
    top, bot = (ends[0], ends[1]) if ends[0, 1] < ends[1, 1] else (ends[1], ends[0])
    left, right = (ends[0], ends[1]) if ends[0, 0] < ends[1, 0] else (ends[1], ends[0])
    iso = lambda v: float(np.linalg.norm((v - gap) * np.array([aspect, 1.0])))
    chord = float(np.linalg.norm(pts_iso[-1] - pts_iso[0]))
    length = float(np.linalg.norm(np.diff(pts_iso, axis=0), axis=1).sum())
    return {
        "cx": float(c[0]), "cy": float(c[1]), "theta": theta,
        "curv": length / max(chord, 1e-6), "len": length,
        "top_gap": iso(top), "left_gap": iso(left),
        "bot_y": float(bot[1]), "right_x": float(right[0]),
    }


def _scores(f: dict) -> dict[str, float]:
    """기준값은 샘플 7장(고전 CV·U-Net 결과)의 실측 특징량에 맞췄다."""
    L = min(f["len"] / 0.5, 1.0)
    return {
        # 손가락 뿌리 바로 아래, 소지 쪽 끝까지 가는 비스듬한 가로선
        "heart": _g(f["theta"], 20, 25) * _g(f["cy"], 0.15, 0.10) * _g(f["right_x"], 1.0, 0.3)
                 * _g(f["cx"], 0.7, 0.25) * L,
        # 엄지-검지 틈에서 시작해 손바닥을 가로지르는 선
        "head": _g(f["theta"], 25, 25) * _g(f["cy"], 0.28, 0.15) * _g(f["left_gap"], 0.0, 0.3)
                * _g(f["right_x"], 0.7, 0.35) * L,
        # 엄지 두덩이를 감싸며 내려가는 선. 위쪽이 끊겨 시작점이 틈에서 멀어도 어느 정도 허용
        "life": _g(f["theta"], 65, 28) * (0.4 + 0.6 * _g(f["top_gap"], 0.0, 0.25))
                * _g(f["cx"], 0.25, 0.25) * _g(f["bot_y"], 0.85, 0.3) * L,
        # 중지·약지 아래 중앙을 거의 수직으로 지나 손목 쪽까지 내려오는 선
        "fate": _g(f["theta"], 88, 10) * _g(f["cx"], 0.55, 0.12) * _g(f["bot_y"], 0.9, 0.2)
                * (1.0 if f["top_gap"] > 0.4 else 0.2) * _g(f["curv"], 1.0, 0.2) * L,
    }


MIN_SCORE = {"heart": 0.12, "head": 0.12, "life": 0.12, "fate": 0.35}


def classify(lines: list[Polyline], lm: np.ndarray) -> list[Polyline]:
    feats = [_features(l, lm) for l in lines]
    table = [_scores(f) for f in feats]
    for l in lines:
        l.kind, l.confidence = "minor", 0.0
    # 점수가 높은 (선, 종류) 쌍부터 하나씩 확정 — 종류별 하나, 선별 하나
    pairs = sorted(((s, i, k) for i, sc in enumerate(table) for k, s in sc.items()), reverse=True)
    used_lines, used_kinds = set(), set()
    for s, i, k in pairs:
        if s < MIN_SCORE[k]:
            continue
        if i in used_lines or k in used_kinds:
            continue
        lines[i].kind, lines[i].confidence = k, float(min(s / 0.6, 1.0))
        used_lines.add(i)
        used_kinds.add(k)
    return lines
