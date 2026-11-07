// 손금(표준 손바닥 좌표 2D) → 3D 손 표면 위 점.
//
// 1) 손목·엄지 뿌리 2개·네 손가락 뿌리, 7개 랜드마크를 기준점으로 thin-plate spline을 맞춘다.
//    표준 좌표(x 오른쪽, y 아래) → 모델 손바닥 평면(x 오른쪽, y 위).
//    기준점 사이는 부드럽게 휘고, 기준점 밖(손바닥 두덩이 등)은 거의 아핀으로 이어진다.
// 2) 그 (x, y)에서 -z 방향으로 광선을 쏴 손 표면에 붙인다. 엄지가 손바닥 앞에 나와 있으므로
//    여러 면에 맞으면 손바닥 표면 높이에 가장 가까운 앞면을 고른다.
import * as THREE from "three";
import type { Point } from "./protocol";

export const ANCHORS = [0, 1, 2, 5, 9, 13, 17];

function U(r2: number) { return r2 <= 1e-12 ? 0 : r2 * Math.log(r2) * 0.5; } // r² log r

/** 작은 연립방정식 풀이 (가우스 소거, 부분 피벗). */
function solve(A: number[][], b: number[]): number[] {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    const d = M[c][c] || 1e-12;
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = M[r][c] / d;
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  return M.map((row, i) => row[n] / (row[i] || 1e-12));
}

export class ThinPlateSpline {
  private wx: number[] = [];
  private wy: number[] = [];
  constructor(private src: Point[], dst: Point[], reg = 1e-4) {
    const n = src.length;
    const A: number[][] = [];
    for (let i = 0; i < n; i++) {
      const row: number[] = [];
      for (let j = 0; j < n; j++) {
        const dx = src[i][0] - src[j][0], dy = src[i][1] - src[j][1];
        row.push(U(dx * dx + dy * dy) + (i === j ? reg : 0));
      }
      row.push(1, src[i][0], src[i][1]);
      A.push(row);
    }
    for (let k = 0; k < 3; k++) {
      const row = src.map((s) => (k === 0 ? 1 : s[k - 1]));
      A.push([...row, 0, 0, 0]);
    }
    this.wx = solve(A, [...dst.map((d) => d[0]), 0, 0, 0]);
    this.wy = solve(A, [...dst.map((d) => d[1]), 0, 0, 0]);
  }
  map([x, y]: Point): Point {
    const n = this.src.length;
    let fx = this.wx[n] + this.wx[n + 1] * x + this.wx[n + 2] * y;
    let fy = this.wy[n] + this.wy[n + 1] * x + this.wy[n + 2] * y;
    for (let i = 0; i < n; i++) {
      const dx = x - this.src[i][0], dy = y - this.src[i][1];
      const u = U(dx * dx + dy * dy);
      fx += this.wx[i] * u;
      fy += this.wy[i] * u;
    }
    return [fx, fy];
  }
}

export interface SurfacePoint { pos: THREE.Vector3; normal: THREE.Vector3; }

export class PalmProjector {
  private ray = new THREE.Raycaster();
  private palmZ: number;

  constructor(private mesh: THREE.Mesh, private modelLandmarks: THREE.Vector3[]) {
    // 손바닥 표면 높이: 손바닥 중심에서 쏜 광선이 처음 맞는 곳
    this.palmZ = this.hitZ(0, 0) ?? 0.17;
  }

  private hits(x: number, y: number) {
    this.ray.set(new THREE.Vector3(x, y, 5), new THREE.Vector3(0, 0, -1));
    return this.ray.intersectObject(this.mesh, false);
  }
  private hitZ(x: number, y: number) { return this.hits(x, y)[0]?.point.z ?? null; }

  /** 이 손금에 맞춘 표준 좌표 → 모델 손바닥 평면 변환. */
  fit(canonLandmarks: Point[]) {
    const src = ANCHORS.map((i) => canonLandmarks[i]);
    const dst = ANCHORS.map((i): Point => [this.modelLandmarks[i].x, this.modelLandmarks[i].y]);
    // 표준 좌표는 y가 아래, 모델은 y가 위 → y를 뒤집어 넣는다
    return new ThinPlateSpline(src.map(([x, y]) => [x, -y] as Point), dst);
  }

  /** 표준 좌표 점 하나를 손 표면에 붙인다. 손 밖이면 null. */
  project(tps: ThinPlateSpline, p: Point, lift = 0.012): SurfacePoint | null {
    const [x, y] = tps.map([p[0], -p[1]]);
    const hs = this.hits(x, y).filter((h) => h.face && h.face.normal.z > 0.15);
    if (!hs.length) return null;
    // 엄지 같은 앞쪽 돌출부를 피해 손바닥 높이에 가장 가까운 면
    const h = hs.reduce((a, b) => (Math.abs(b.point.z - this.palmZ) < Math.abs(a.point.z - this.palmZ) ? b : a));
    const n = h.face!.normal.clone().transformDirection(this.mesh.matrixWorld).normalize();
    return { pos: h.point.clone().addScaledVector(n, lift), normal: n };
  }
}
