"""MediaPipe Hand Landmarker 래퍼 + 손바닥 정면·정지 판정 게이트."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "assets" / "hand_landmarker.task"

# 랜드마크 인덱스
WRIST, THUMB_CMC, THUMB_MCP = 0, 1, 2
INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP = 5, 9, 13, 17
FINGERTIPS = (4, 8, 12, 16, 20)


@dataclass
class HandObservation:
    landmarks_px: np.ndarray   # (21, 2) 이미지 픽셀 좌표
    landmarks_z: np.ndarray    # (21,) 상대 깊이
    handedness: str            # MediaPipe 라벨 ("Left" | "Right")
    score: float
    image_size: tuple[int, int]  # (w, h)

    @property
    def thumb_on_left(self) -> bool:
        """손목→중지뿌리 축 기준으로 엄지가 화면 왼쪽에 있는가."""
        lm = self.landmarks_px
        axis = lm[MIDDLE_MCP] - lm[WRIST]
        thumb = lm[THUMB_MCP] - lm[WRIST]
        # 이미지 좌표(y 아래) 외적: 음수면 엄지가 축의 왼쪽
        # 예) 축 (0,-1) 위쪽, 엄지 (-1,0) 왼쪽 → 0*0 - (-1)(-1) = -1
        return float(axis[0] * thumb[1] - axis[1] * thumb[0]) < 0

    @property
    def palm_facing(self) -> bool:
        """손바닥이 카메라를 향하는가.

        미러 아닌 사진에서 손바닥이 보이면 오른손은 엄지가 왼쪽에 있고 MediaPipe는 'Left'로 라벨한다
        (라벨이 미러 입력을 가정하기 때문). 미러 영상이면 엄지 위치와 라벨이 함께 뒤집혀 같은 식이 성립한다.
        샘플 7장(손바닥 사진)으로 확인한 규약: 라벨 'Right' ↔ 엄지 오른쪽.
        """
        return (self.handedness == "Right") != self.thumb_on_left

    def fully_visible(self, margin: float = 0.03) -> bool:
        """손끝·손목을 포함한 손 전체가 프레임 안쪽에 있는가.
        랜드마크는 점이라 실제 손가락 끝·손목 살은 조금 더 바깥까지 있으므로 여유를 둔다."""
        w, h = self.image_size
        lm = self.landmarks_px
        pad = 0.12 * self.palm_size_px     # 손끝 살·손목 아래 여유
        lo = lm.min(axis=0) - pad
        hi = lm.max(axis=0) + pad
        hi[1] = max(hi[1], lm[WRIST, 1] + 0.25 * self.palm_size_px)
        return bool(lo[0] >= margin * w and lo[1] >= margin * h and hi[0] <= (1 - margin) * w
                    and hi[1] <= (1 - margin) * h + 0.25 * self.palm_size_px)

    @property
    def palm_size_px(self) -> float:
        lm = self.landmarks_px
        return float(np.linalg.norm(lm[MIDDLE_MCP] - lm[WRIST]))


class HandTracker:
    """mode="video"는 웹캠 루프용(타임스탬프 필요), "image"는 단일 사진용."""

    def __init__(self, model_path: str | Path = DEFAULT_MODEL, mode: str = "video",
                 min_detection: float = 0.5, min_tracking: float = 0.5):
        running_mode = vision.RunningMode.VIDEO if mode == "video" else vision.RunningMode.IMAGE
        opts = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            running_mode=running_mode,
            num_hands=1,
            min_hand_detection_confidence=min_detection,
            min_hand_presence_confidence=min_detection,
            min_tracking_confidence=min_tracking,
        )
        self.mode = mode
        self._lm = vision.HandLandmarker.create_from_options(opts)
        self._last_ts = -1

    def detect(self, bgr: np.ndarray, timestamp_ms: int | None = None) -> HandObservation | None:
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        if self.mode == "video":
            ts = int(timestamp_ms or 0)
            ts = max(ts, self._last_ts + 1)   # 단조 증가 보장
            self._last_ts = ts
            res = self._lm.detect_for_video(image, ts)
        else:
            res = self._lm.detect(image)
        if not res.hand_landmarks:
            return None
        lms = res.hand_landmarks[0]
        pts = np.array([[p.x * w, p.y * h] for p in lms], dtype=np.float32)
        z = np.array([p.z for p in lms], dtype=np.float32)
        cat = res.handedness[0][0]
        return HandObservation(pts, z, cat.category_name, float(cat.score), (w, h))

    def close(self):
        self._lm.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class StillnessGate:
    """손바닥이 정면·충분한 크기·정지 상태로 hold_frames 동안 유지되면 열린다."""

    def __init__(self, window: int = 15, motion_thresh: float = 0.012,
                 min_size_ratio: float = 0.22, max_size_ratio: float = 0.50, edge_margin: float = 0.03):
        self.window = window
        self.motion_thresh = motion_thresh     # 손 크기 대비 평균 이동량
        self.min_size_ratio = min_size_ratio   # 손목~중지뿌리 / 프레임 높이
        self.max_size_ratio = max_size_ratio   # 이보다 크면 초점이 흐려지고 손이 화면 밖으로 잘린다
        self.edge_margin = edge_margin         # 손 전체(손끝 포함)가 프레임 가장자리에서 떨어져야 하는 비율
        self._hist: deque[np.ndarray] = deque(maxlen=window)

    def reset(self):
        self._hist.clear()

    def update(self, obs: HandObservation | None) -> tuple[bool, str, float]:
        """(열림 여부, 안내 문구, 진행도 0~1)."""
        if obs is None:
            self.reset()
            return False, "손바닥을 카메라에 보여주세요", 0.0
        if not obs.palm_facing:
            self.reset()
            return False, "손바닥 쪽을 보여주세요", 0.0
        size = obs.palm_size_px
        if size < self.min_size_ratio * obs.image_size[1]:
            self.reset()
            return False, "손을 조금 더 가까이", 0.0
        if size > self.max_size_ratio * obs.image_size[1]:
            self.reset()
            return False, "손을 조금 더 멀리", 0.0
        if not obs.fully_visible(self.edge_margin):
            self.reset()
            return False, "손 전체가 화면 안에 들어오게 해주세요", 0.0
        self._hist.append(obs.landmarks_px.copy())
        if len(self._hist) < 2:
            return False, "그대로 멈춰주세요", 0.0
        arr = np.stack(self._hist)
        motion = float(np.linalg.norm(np.diff(arr, axis=0), axis=2).mean()) / size
        if motion > self.motion_thresh:
            # 움직이면 처음부터
            last = self._hist[-1]
            self._hist.clear()
            self._hist.append(last)
            return False, "그대로 멈춰주세요", 0.0
        progress = len(self._hist) / self.window
        return progress >= 1.0, "그대로 멈춰주세요", min(progress, 1.0)
