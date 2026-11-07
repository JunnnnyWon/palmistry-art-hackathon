// 서버(/ws/track)와 주고받는 메시지 형식. server/main.py 와 맞춘다.

export type Point = [number, number];

export type LineKind = "heart" | "head" | "life" | "fate" | "minor";

export interface PalmLine {
  kind: LineKind;
  confidence: number;
  strength: number;
  points: Point[]; // 표준 손바닥 좌표 0~1 (엄지 왼쪽, 손가락 위)
}

export interface PalmLineSet {
  handedness: string;
  source: string;
  captured_at: number;
  landmarks: Point[]; // 21점, 표준 손바닥 좌표
  outline: Point[] | null;
  mirrored: boolean; // 정면화 때 좌우를 뒤집었는가 → 3D 손도 뒤집어 화면 미리보기와 같은 방향으로
  lines: PalmLine[];
}

export interface TrackMsg {
  type: "track";
  message: string;
  progress: number;
  ready: boolean;
  size: [number, number];
  hand: { landmarks: Point[]; handedness: string; palm_facing: boolean } | null; // landmarks: 프레임 기준 0~1
}

export interface CaptureMsg {
  type: "capture";
  session: string;
  nickname?: string;  // 이 관객의 이름 (감정 + 이름, 서버가 붙인다)
  palm: PalmLineSet;
  method: string;
  ms: number;
  frames: number;
  outline: string;
  reason: string;
}

export interface InterpStep {
  step: number;           // 1~10
  temperature: number;
  min_p: number;
}

export interface InterpMsg extends InterpStep {
  type: "interp";
  session: string;
  text: string;
  foreign_ratio: number;  // 글자 중 외국 문자 비율 → 자막 흔들림
  hangul_ratio: number;
  finish: string | null;
  provider: string | null;
  ms: number;
  error: string | null;
  borrowed_from: number | null;
}

/** 서버가 들고 있는 해석 단계 상태. 노브·방향키 어느 쪽에서 바뀌어도 모두에게 온다. */
export interface StepMsg {
  type: "step";
  room?: string;      // 방 이름 (개인 방이면 solo-...)
  step: number;
  steps: number;
  phase: "waiting" | "capturing" | "result";
  session: string | null;
  nickname?: string;  // 지금 관객의 이름
  temperature: number;
  min_p: number;
  ready: number[];
  source: string;     // knob | keyboard | server | rejected ...
  displays: number;
  knobs: number;
}

export type ServerMsg =
  | StepMsg
  | TrackMsg
  | CaptureMsg
  | InterpMsg
  | { type: "interp_start"; session: string; description: string; steps: InterpStep[] }
  | { type: "interp_done"; session: string; failed: number[]; seconds: number }
  | { type: "interp_error"; session: string; message: string }
  | { type: "capturing"; reason: string }
  | { type: "reset_ok" }
  | { type: "pong"; unet: string; method: string }
  | { type: "error"; message: string };

export const LINE_COLORS: Record<LineKind, string> = {
  heart: "#ff5a6e",
  head: "#5ac8ff",
  life: "#8ce678",
  fate: "#ffd25a",
  minor: "#96a0aa",
};

export const LINE_NAMES: Record<LineKind, string> = {
  heart: "감정선",
  head: "두뇌선",
  life: "생명선",
  fate: "운명선",
  minor: "잔선",
};

export const HAND_CONNECTIONS: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [0, 17], [17, 18], [18, 19], [19, 20],
];
