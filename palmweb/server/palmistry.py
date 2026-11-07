"""손금 특징 계산과 문장 묘사 (AI 입력).

사진이 아니라 추출된 손금 벡터(PalmLineSet JSON)에서 특징을 재서 한국어 묘사로 바꾼다.
어떤 손금이 어떤 해석을 낳는지 통제·설명할 수 있게 하려는 것이다 (명세서 4.4절).

좌표: 표준 손바닥 좌표 (0~1, x 오른쪽, y 아래, 엄지 왼쪽).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

KIND_NAME = {"heart": "감정선", "head": "두뇌선", "life": "생명선", "fate": "운명선"}
ORDER = ("life", "head", "heart", "fate")

# 구간 경계. 2026-11-06 캡처 14건(샘플 사진 + 웹캠)의 삼분위 근처로 잡은 초기값.
# 관객 데이터가 쌓이면 sessions/ 의 분포로 다시 정한다.
LEN_SHORT, LEN_LONG = 0.50, 0.57           # 선 길이 ÷ 손바닥 높이(손목~중지 뿌리). 분포 0.43~0.64
CURV_STRAIGHT, CURV_BENT = 1.02, 1.04      # 선 길이 ÷ 양끝 직선거리. 분포 1.01~1.06
GAP_NARROW, GAP_WIDE = 0.15, 0.18          # 중지 아래에서 감정선~두뇌선 거리 ÷ 손바닥 높이. 분포 0.14~0.19
STR_FAINT, STR_DEEP = 0.55, 0.72           # 릿지 응답 평균
MINOR_FEW, MINOR_MANY = 3, 6               # 잔선 개수


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _length(pts):
    return sum(_dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))


@dataclass
class LineFeature:
    kind: str
    present: bool
    length: float = 0.0       # 손바닥 높이 대비
    curvature: float = 1.0
    strength: float = 0.0
    start: str = ""
    end: str = ""

    @property
    def length_word(self):
        return "짧" if self.length < LEN_SHORT else ("길" if self.length > LEN_LONG else "보통 길이")

    @property
    def curve_word(self):
        return "곧게 뻗어 있다" if self.curvature < CURV_STRAIGHT else (
            "크게 휘어 있다" if self.curvature > CURV_BENT else "완만하게 휘어 있다")

    @property
    def strength_word(self):
        return "옅" if self.strength < STR_FAINT else ("진하" if self.strength > STR_DEEP else "")


@dataclass
class PalmFeatures:
    lines: dict
    minor_count: int
    heart_head_gap: float | None     # 손바닥 높이 대비, None이면 둘 중 하나 없음

    def to_dict(self):
        return {"lines": {k: asdict(v) for k, v in self.lines.items()},
                "minor_count": self.minor_count, "heart_head_gap": self.heart_head_gap}


class _Zones:
    """손바닥 구역 이름: 손가락 뿌리 아래 네 칸, 엄지 두덩이, 새끼손가락 쪽 가장자리, 손목 쪽, 손바닥 가운데."""

    def __init__(self, lm):
        self.lm = lm
        self.mcp = {name: lm[i] for name, i in (("검지", 5), ("중지", 9), ("약지", 13), ("소지", 17))}
        self.mcp_y = sum(p[1] for p in self.mcp.values()) / 4
        self.wrist_y = lm[0][1]
        self.H = max(_dist(lm[0], lm[9]), 1e-6)        # 손바닥 높이 = 손목~중지 뿌리
        self.Hy = max(self.wrist_y - self.mcp_y, 1e-6)  # 구역 판정용 세로 범위
        self.x_index, self.x_pinky = lm[5][0], lm[17][0]
        self.W = max(self.x_pinky - self.x_index, 1e-6)

    def name(self, p):
        rx = (p[0] - self.x_index) / self.W     # 0 = 검지 뿌리, 1 = 소지 뿌리
        ry = (p[1] - self.mcp_y) / self.Hy      # 0 = 손가락 뿌리 라인, 1 = 손목
        if ry > 0.78:
            return "손목 쪽"
        if rx < -0.12:
            return "엄지와 검지 사이" if ry < 0.35 else "엄지 두덩이"
        if rx < 0.10 and ry > 0.10:
            return "엄지와 검지 사이"
        if rx > 1.12:
            return "새끼손가락 쪽 가장자리"
        if ry < 0.42:
            nearest = min(self.mcp, key=lambda k: abs(self.mcp[k][0] - p[0]))
            return f"{nearest} 아래"
        return "손바닥 가운데"


def compute_features(palm: dict) -> PalmFeatures:
    lm = palm["landmarks"]
    z = _Zones(lm)
    best: dict[str, dict] = {}
    for l in palm["lines"]:
        if l["kind"] in KIND_NAME:
            if l["kind"] not in best or len(l["points"]) > len(best[l["kind"]]["points"]):
                best[l["kind"]] = l
    feats = {}
    for kind in ORDER:
        l = best.get(kind)
        if not l or len(l["points"]) < 2:
            feats[kind] = LineFeature(kind, False)
            continue
        pts = l["points"]
        L = _length(pts)
        chord = max(_dist(pts[0], pts[-1]), 1e-6)
        a, b = pts[0], pts[-1]
        # 시작점: 위쪽(손가락 쪽) 끝, 단 감정선은 새끼손가락 쪽 끝에서 시작한다고 본다
        if kind == "heart":
            a, b = (a, b) if a[0] > b[0] else (b, a)
        else:
            a, b = (a, b) if a[1] < b[1] else (b, a)
        feats[kind] = LineFeature(kind, True, round(L / z.H, 3), round(L / chord, 3),
                                  round(float(l.get("strength", 0)), 3), z.name(a), z.name(b))
    gap = None
    if feats["heart"].present and feats["head"].present:
        # 두뇌선은 비스듬히 내려가므로, 중지 뿌리 아래 세로줄에서 두 선의 높이 차를 잰다
        xm = lm[9][0]
        y_at = lambda pts: min(pts, key=lambda p: abs(p[0] - xm))[1]
        gap = round(abs(y_at(best["head"]["points"]) - y_at(best["heart"]["points"])) / z.H, 3)
    minors = sum(1 for l in palm["lines"] if l["kind"] == "minor")
    return PalmFeatures(feats, minors, gap)


def describe(f: PalmFeatures) -> str:
    """특징 → 한국어 묘사 (모델 입력)."""
    out = []
    for kind in ORDER:
        lf: LineFeature = f.lines[kind]
        name = KIND_NAME[kind]
        if not lf.present:
            out.append(f"{name}은 희미하거나 끊겨 있어 잘 보이지 않는다.")
            continue
        size = {"짧": "짧고", "길": "길고", "보통 길이": "보통 길이이고"}[lf.length_word]
        depth = {"진하": " 진하며", "옅": " 옅으며", "": ""}[lf.strength_word]
        route = (f"{lf.start}에서 시작해 {lf.end}에서 끝나고" if lf.start != lf.end
                 else f"{lf.start}에 짧게 놓여 있고")
        out.append(f"{name}은 {size}{depth}, {route}, {lf.curve_word}.")
    if f.heart_head_gap is not None:
        g = "좁다" if f.heart_head_gap < GAP_NARROW else ("넓다" if f.heart_head_gap > GAP_WIDE else "보통이다")
        out.append(f"감정선과 두뇌선 사이 간격은 {g}.")
    m = "적다" if f.minor_count < MINOR_FEW else ("많다" if f.minor_count > MINOR_MANY else "보통이다")
    out.append(f"손바닥의 잔금은 {m}.")
    return " ".join(out)
