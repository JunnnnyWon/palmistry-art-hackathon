"""파이프라인 전 구간이 공유하는 데이터 구조.

좌표 규약 (표준 손바닥 좌표계)
- 0.0~1.0 정규화, x는 오른쪽, y는 아래쪽.
- 손가락이 위, 손목이 아래, 엄지는 항상 왼쪽 (왼손은 좌우 반전해 맞춘다).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

LINE_KINDS = ("heart", "head", "life", "fate", "minor")


@dataclass
class Polyline:
    points: np.ndarray            # (N, 2) 표준 좌표계, 0~1
    kind: str = "minor"           # LINE_KINDS 중 하나
    confidence: float = 0.0       # 0~1, 분류 확신도
    strength: float = 0.0         # 선 위 릿지 응답 평균 (굵고 진한 선일수록 큼)

    @property
    def length(self) -> float:
        if len(self.points) < 2:
            return 0.0
        return float(np.linalg.norm(np.diff(self.points, axis=0), axis=1).sum())


@dataclass
class PalmLineSet:
    lines: list[Polyline]
    handedness: str                   # "Left" | "Right"
    landmarks: np.ndarray             # (21, 2) 표준 좌표계 랜드마크
    roi_image: np.ndarray | None      # 정면화된 손바닥 BGR 이미지 (보관 안 하면 None)
    source: str = "classical"
    captured_at: float = field(default_factory=time.time)
    outline: np.ndarray | None = None   # (N, 2) 표준 좌표 손 윤곽. 렌더·선 확장의 경계로 쓴다

    def by_kind(self, kind: str) -> list[Polyline]:
        return [l for l in self.lines if l.kind == kind]


def to_json(ls: PalmLineSet) -> dict:
    """다음 단계(확장 변조·SVG 출력)에서 쓰는 벡터 데이터. 손바닥 이미지는 넣지 않는다."""
    return {
        "handedness": ls.handedness,
        "source": ls.source,
        "captured_at": ls.captured_at,
        "landmarks": ls.landmarks.astype(float).round(5).tolist(),
        "outline": None if ls.outline is None else ls.outline.astype(float).round(5).tolist(),
        "lines": [
            {"kind": l.kind, "confidence": round(l.confidence, 3), "strength": round(l.strength, 3),
             "points": l.points.astype(float).round(5).tolist()}
            for l in ls.lines
        ],
    }


def from_json(d: dict) -> PalmLineSet:
    return PalmLineSet(
        lines=[Polyline(np.asarray(l["points"], np.float32), l["kind"], l["confidence"], l["strength"])
               for l in d["lines"]],
        handedness=d["handedness"], landmarks=np.asarray(d["landmarks"], np.float32),
        roi_image=None, source=d.get("source", "classical"), captured_at=d.get("captured_at", 0.0),
        outline=None if d.get("outline") is None else np.asarray(d["outline"], np.float32),
    )
