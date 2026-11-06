"""스켈레톤 → 폴리라인.

연결 성분마다 '가장 긴 경로'를 뽑아 한 선으로 만들고, 남은 가지에서 다시 가장 긴 경로를 뽑는다.
이렇게 하면 짧은 잔가지는 자연스럽게 떨어지고 주요 선은 한 줄로 이어진다.
그다음 끝점이 가깝고 방향이 맞는 조각을 이어 붙이고, 점을 줄인 뒤 부드럽게 만든다.
"""
from __future__ import annotations

import math

import networkx as nx
import numpy as np

from ..model import Polyline

_NEI = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _graph(skel: np.ndarray) -> nx.Graph:
    ys, xs = np.nonzero(skel)
    on = set(zip(ys.tolist(), xs.tolist()))
    G = nx.Graph()
    G.add_nodes_from(on)
    for y, x in on:
        for dy, dx in _NEI:
            q = (y + dy, x + dx)
            if q in on:
                G.add_edge((y, x), q, weight=math.sqrt(2) if dy and dx else 1.0)
    return G


def _longest_path(G: nx.Graph) -> tuple[list, float]:
    """이중 다익스트라로 근사한 가장 긴 최단경로 (트리에서는 정확한 지름)."""
    start = next(iter(G.nodes))
    d = nx.single_source_dijkstra_path_length(G, start)
    a = max(d, key=d.get)
    d, paths = nx.single_source_dijkstra(G, a)
    b = max(d, key=d.get)
    return paths[b], d[b]


def skeleton_paths(skel: np.ndarray, min_len_px: float) -> list[np.ndarray]:
    G = _graph(skel)
    out: list[np.ndarray] = []
    stack = [G.subgraph(c).copy() for c in nx.connected_components(G)]
    while stack:
        sub = stack.pop()
        if sub.number_of_nodes() < min_len_px * 0.7:
            continue
        path, length = _longest_path(sub)
        if length < min_len_px:
            continue
        out.append(np.array([(x, y) for y, x in path], dtype=np.float32))
        sub.remove_nodes_from(path)
        stack.extend(sub.subgraph(c).copy() for c in nx.connected_components(sub))
    return out


def _resample(pts: np.ndarray, step: float) -> np.ndarray:
    if len(pts) < 2:
        return pts
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    if s[-1] < step:
        return pts[[0, -1]]
    t = np.arange(0, s[-1], step)
    t = np.append(t, s[-1])
    return np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], axis=1).astype(np.float32)


def _chaikin(pts: np.ndarray, it: int = 2) -> np.ndarray:
    for _ in range(it):
        if len(pts) < 3:
            return pts
        q = 0.75 * pts[:-1] + 0.25 * pts[1:]
        r = 0.25 * pts[:-1] + 0.75 * pts[1:]
        mid = np.empty((2 * len(q), 2), np.float32)
        mid[0::2], mid[1::2] = q, r
        pts = np.vstack([pts[:1], mid, pts[-1:]])
    return pts


def _end_dir(pts: np.ndarray, at_start: bool, k: int = 6) -> np.ndarray:
    """끝점에서 바깥쪽을 향하는 단위 방향."""
    k = min(k, len(pts) - 1)
    v = pts[0] - pts[k] if at_start else pts[-1] - pts[-1 - k]
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def split_at_corners(pts: np.ndarray, k: int = 4, max_turn_deg: float = 55.0) -> list[np.ndarray]:
    """경로가 급하게 꺾이는 지점(교차점에서 다른 주름으로 넘어간 곳)에서 자른다."""
    n = len(pts)
    if n < 2 * k + 2:
        return [pts]
    cuts = []
    cos_lim = math.cos(math.radians(max_turn_deg))
    i = k
    while i < n - k:
        a = pts[i] - pts[i - k]
        b = pts[i + k] - pts[i]
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na > 0 and nb > 0 and float(a @ b) / (na * nb) < cos_lim:
            # 창 안에서 가장 크게 꺾인 점을 자름점으로
            best, best_c = i, 2.0
            for j in range(i, min(i + k, n - k)):
                a2, b2 = pts[j] - pts[j - k], pts[j + k] - pts[j]
                c = float(a2 @ b2) / max(np.linalg.norm(a2) * np.linalg.norm(b2), 1e-6)
                if c < best_c:
                    best, best_c = j, c
            cuts.append(best)
            i = best + k
        else:
            i += 1
    if not cuts:
        return [pts]
    bounds = [0] + cuts + [n - 1]
    return [pts[a:b + 1] for a, b in zip(bounds[:-1], bounds[1:]) if b - a >= 2]


def merge_fragments(paths: list[np.ndarray], max_gap: float, max_angle_deg: float = 35.0) -> list[np.ndarray]:
    """끝점이 max_gap 안이고 서로 마주 보는 방향이면 이어 붙인다 (반복)."""
    paths = [p for p in paths if len(p) >= 2]
    cos_lim = math.cos(math.radians(max_angle_deg))
    changed = True
    while changed:
        changed = False
        best = None
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                a, b = paths[i], paths[j]
                for ea in (True, False):
                    for eb in (True, False):
                        pa = a[0] if ea else a[-1]
                        pb = b[0] if eb else b[-1]
                        gap = float(np.linalg.norm(pb - pa))
                        if gap > max_gap:
                            continue
                        da, db = _end_dir(a, ea), _end_dir(b, eb)
                        # a의 끝 방향과 b의 끝 방향이 서로 반대(마주 봄)여야 자연스럽게 이어짐
                        if float(-da @ db) < cos_lim:
                            continue
                        if gap > 2:
                            conn = (pb - pa) / gap
                            if float(da @ conn) < cos_lim * 0.8:
                                continue
                        score = gap
                        if best is None or score < best[0]:
                            best = (score, i, j, ea, eb)
        if best:
            _, i, j, ea, eb = best
            a, b = paths[i], paths[j]
            a2 = a[::-1] if ea else a          # a의 붙일 끝이 맨 뒤로
            b2 = b if eb else b[::-1]          # b의 붙일 끝이 맨 앞으로
            merged = np.vstack([a2, b2])
            paths = [p for k, p in enumerate(paths) if k not in (i, j)] + [merged]
            changed = True
    return paths


def to_polylines(skel: np.ndarray, ridge: np.ndarray, size: int,
                 min_len_ratio: float = 0.07, merge_gap_ratio: float = 0.05,
                 final_min_ratio: float = 0.10, max_lines: int = 9) -> list[Polyline]:
    raw = skeleton_paths(skel, min_len_px=min_len_ratio * size)
    raw = [_resample(p, 3.0) for p in raw]
    raw = [q for p in raw for q in split_at_corners(p)]
    merged = merge_fragments(raw, max_gap=merge_gap_ratio * size)
    lines: list[Polyline] = []
    for p in merged:
        p = _resample(p, 6.0)
        p = _chaikin(p, 2)
        pl = Polyline(points=(p / size).astype(np.float32))
        if pl.length < final_min_ratio:
            continue
        chord = float(np.linalg.norm(pl.points[-1] - pl.points[0]))
        if pl.length / max(chord, 1e-6) > 2.2:   # 꺾임을 잘라내고도 꼬불꼬불하면 잔주름 그물
            continue
        xi = np.clip(p[:, 0].astype(int), 0, size - 1)
        yi = np.clip(p[:, 1].astype(int), 0, size - 1)
        pl.strength = float(ridge[yi, xi].mean())
        lines.append(pl)
    lines.sort(key=lambda l: l.length * (0.5 + l.strength), reverse=True)
    return lines[:max_lines]
