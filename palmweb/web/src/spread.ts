// 뿌리 확산 계산: 손금을 씨앗으로, 손 메시 정점을 따라 퍼지는 도달 시간과 뿌리 나무.
//
// 1) 메시 정점 그래프(간선 = 삼각형 변, 가중치 = 길이)를 만든다.
// 2) 손금 선의 점마다 가장 가까운 정점을 씨앗으로 둔다. 씨앗마다 속도가 다르다
//    (길고 진한 선일수록 빠름, 잔선은 절반). 다익스트라로 정점마다 '도착 시간'을 구한다.
// 3) 도착 시간을 0~1로 정규화한다. 그래서 마지막 단계에서는 누구나 손 전체가 덮이지만,
//    덮이는 순서와 모양은 손금에 따라 다르다.
// 4) 다익스트라가 남긴 '어디서 왔는지(부모)'를 이으면 손금에서 갈라져 나가는 나무가 된다.
//    한 정점을 거쳐 간 후손 수가 많을수록 굵은 줄기다.
import * as THREE from "three";

export interface Seed { pos: THREE.Vector3; speed: number; }

// 공간적으로 부드러운 3D 값 노이즈 (0~1). 간선 길이에 곱해 뿌리 경로를 구불구불하게 만든다.
// 메시가 규칙적인 격자로 세분화되어 있어서, 노이즈 없이는 최단 경로가 평행한 빗금처럼 보인다.
function hash3(x: number, y: number, z: number) {
  const h = Math.sin(x * 127.1 + y * 311.7 + z * 74.7) * 43758.5453;
  return h - Math.floor(h);
}
function valueNoise(x: number, y: number, z: number) {
  const xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z);
  const xf = x - xi, yf = y - yi, zf = z - zi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf), w = zf * zf * (3 - 2 * zf);
  let out = 0;
  for (let dx = 0; dx <= 1; dx++) for (let dy = 0; dy <= 1; dy++) for (let dz = 0; dz <= 1; dz++) {
    const k = (dx ? u : 1 - u) * (dy ? v : 1 - v) * (dz ? w : 1 - w);
    out += k * hash3(xi + dx, yi + dy, zi + dz);
  }
  return out;
}
const NOISE_FREQ = 9;       // 손목~중지 뿌리 = 1 단위에 노이즈 무늬 약 9개
const NOISE_AMP = 0.85;     // 간선 길이 배율 범위: 1 ± 0.85/2

export interface RootTier {
  positions: Float32Array;   // 선분 [x0,y0,z0, x1,y1,z1, ...], 도착 시간 순으로 정렬
  arrivals: Float32Array;    // 선분마다 (끝점의) 도착 시간, 오름차순
  weight: number;            // 이 층의 굵기 등급 0~1
}

class MinHeap {
  private t: number[] = [];
  private v: number[] = [];
  private s: number[] = [];
  get size() { return this.t.length; }
  push(time: number, node: number, speed: number) {
    const { t, v, s } = this;
    let i = t.length;
    t.push(time); v.push(node); s.push(speed);
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (t[p] <= t[i]) break;
      [t[p], t[i]] = [t[i], t[p]]; [v[p], v[i]] = [v[i], v[p]]; [s[p], s[i]] = [s[i], s[p]];
      i = p;
    }
  }
  pop(): [number, number, number] {
    const { t, v, s } = this;
    const out: [number, number, number] = [t[0], v[0], s[0]];
    const lt = t.pop()!, lv = v.pop()!, ls = s.pop()!;
    if (t.length) {
      t[0] = lt; v[0] = lv; s[0] = ls;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < t.length && t[l] < t[m]) m = l;
        if (r < t.length && t[r] < t[m]) m = r;
        if (m === i) break;
        [t[m], t[i]] = [t[i], t[m]]; [v[m], v[i]] = [v[i], v[m]]; [s[m], s[i]] = [s[i], s[m]];
        i = m;
      }
    }
    return out;
  }
}

export class SpreadField {
  /** 병합된(위치가 같은 정점을 하나로 본) 정점 수 */
  readonly n: number;
  /** 지오메트리 정점 → 병합 정점 번호. UV 이음새에서 갈라진 정점들이 같은 번호를 갖는다 */
  readonly canonOf: Int32Array;
  private pos: Float32Array;
  private nrm: Float32Array;
  readonly adjStart: Int32Array;   // 정점 v의 이웃: adj[adjStart[v] .. adjStart[v+1])
  readonly adj: Int32Array;
  private adjW: Float32Array;
  private grid = new Map<string, number[]>();
  private cell: number;
  /** 정점마다 손 안쪽 위치 (표면에서 두께의 일부만큼 들어간 점). */
  readonly inner: Float32Array;
  /** 정점마다 손 두께 어림 (0.01~0.35). */
  readonly thick: Float32Array;
  /** 정점마다 안쪽 위치가 표면에서 들어간 깊이. */
  readonly innerDepth: Float32Array;
  /** 병합 정점의 표면 위치 */
  get positions() { return this.pos; }

  constructor(geometry: THREE.BufferGeometry) {
    const p = geometry.getAttribute("position") as THREE.BufferAttribute;
    const nAttr = geometry.getAttribute("normal") as THREE.BufferAttribute;
    const total = p.count;
    // 위치가 같은 정점을 하나로 (텍스처 UV 이음새에서 메시가 갈라져 있으면 그래프가 끊긴다)
    const canonOf = new Int32Array(total);
    const firstOf: number[] = [];
    const key2 = new Map<string, number>();
    for (let i = 0; i < total; i++) {
      const k = `${Math.round(p.getX(i) * 1e4)},${Math.round(p.getY(i) * 1e4)},${Math.round(p.getZ(i) * 1e4)}`;
      let c = key2.get(k);
      if (c === undefined) { c = firstOf.length; key2.set(k, c); firstOf.push(i); }
      canonOf[i] = c;
    }
    this.n = firstOf.length;
    this.canonOf = canonOf;
    this.pos = new Float32Array(this.n * 3);
    this.nrm = new Float32Array(this.n * 3);
    for (let i = 0; i < total; i++) {
      const c = canonOf[i];
      if (firstOf[c] === i) { this.pos[3 * c] = p.getX(i); this.pos[3 * c + 1] = p.getY(i); this.pos[3 * c + 2] = p.getZ(i); }
      // 이음새 양쪽 법선을 평균한다
      this.nrm[3 * c] += nAttr.getX(i); this.nrm[3 * c + 1] += nAttr.getY(i); this.nrm[3 * c + 2] += nAttr.getZ(i);
    }
    for (let c = 0; c < this.n; c++) {
      const L = Math.hypot(this.nrm[3 * c], this.nrm[3 * c + 1], this.nrm[3 * c + 2]) || 1;
      this.nrm[3 * c] /= L; this.nrm[3 * c + 1] /= L; this.nrm[3 * c + 2] /= L;
    }
    const index = geometry.getIndex();
    if (!index) throw new Error("손 메시에 인덱스가 없습니다");
    // 간선 모으기 (중복 제거)
    const edges = new Set<number>();
    const ia = index.array;
    const key = (a: number, b: number) => (a < b ? a * this.n + b : b * this.n + a);
    for (let i = 0; i < ia.length; i += 3) {
      const a = canonOf[ia[i]], b = canonOf[ia[i + 1]], c = canonOf[ia[i + 2]];
      if (a !== b) edges.add(key(a, b));
      if (b !== c) edges.add(key(b, c));
      if (c !== a) edges.add(key(c, a));
    }
    const deg = new Int32Array(this.n);
    for (const k of edges) { deg[Math.floor(k / this.n)]++; deg[k % this.n]++; }
    this.adjStart = new Int32Array(this.n + 1);
    for (let i = 0; i < this.n; i++) this.adjStart[i + 1] = this.adjStart[i] + deg[i];
    this.adj = new Int32Array(this.adjStart[this.n]);
    this.adjW = new Float32Array(this.adjStart[this.n]);
    const fill = this.adjStart.slice(0, this.n);
    for (const k of edges) {
      const a = Math.floor(k / this.n), b = k % this.n;
      const len = Math.hypot(this.pos[3 * a] - this.pos[3 * b], this.pos[3 * a + 1] - this.pos[3 * b + 1], this.pos[3 * a + 2] - this.pos[3 * b + 2]);
      const mx = (this.pos[3 * a] + this.pos[3 * b]) / 2, my = (this.pos[3 * a + 1] + this.pos[3 * b + 1]) / 2, mz = (this.pos[3 * a + 2] + this.pos[3 * b + 2]) / 2;
      const n1 = valueNoise(mx * NOISE_FREQ, my * NOISE_FREQ, mz * NOISE_FREQ);
      const n2 = valueNoise(mx * NOISE_FREQ * 2.7 + 17, my * NOISE_FREQ * 2.7 + 5, mz * NOISE_FREQ * 2.7 + 11);
      const w = len * (1 - NOISE_AMP / 2 + NOISE_AMP * (0.65 * n1 + 0.35 * n2));
      this.adj[fill[a]] = b; this.adjW[fill[a]++] = w;
      this.adj[fill[b]] = a; this.adjW[fill[b]++] = w;
    }
    // 가장 가까운 정점 찾기용 격자
    let sum = 0;
    for (let i = 0; i < this.adjW.length; i++) sum += this.adjW[i];
    this.cell = (sum / this.adjW.length) * 4;
    for (let i = 0; i < this.n; i++) {
      const k = this.cellKey(this.pos[3 * i], this.pos[3 * i + 1], this.pos[3 * i + 2]);
      let bucket = this.grid.get(k);
      if (!bucket) this.grid.set(k, (bucket = []));
      bucket.push(i);
    }
    this.thick = this.thickness();
    this.innerDepth = new Float32Array(this.n);
    this.inner = this.innerPositions();
  }

  /** 병합 정점별 값 → 지오메트리 정점별 값 (속성으로 넘길 때) */
  expand(values: Float32Array) {
    const out = new Float32Array(this.canonOf.length);
    for (let i = 0; i < out.length; i++) out[i] = values[this.canonOf[i]];
    return out;
  }

  /** 정점마다 손 두께를 어림한다: 법선 반대쪽에 있고 법선이 마주보는 가장 가까운 정점까지 거리. */
  private thickness() {
    const th = new Float32Array(this.n).fill(0.1);
    const { pos, nrm } = this;
    for (let i = 0; i < this.n; i++) {
      const x = pos[3 * i], y = pos[3 * i + 1], z = pos[3 * i + 2];
      const nx = nrm[3 * i], ny = nrm[3 * i + 1], nz = nrm[3 * i + 2];
      const cx = Math.floor(x / this.cell), cy = Math.floor(y / this.cell), cz = Math.floor(z / this.cell);
      let best = Infinity;
      for (let dx = -3; dx <= 3; dx++) for (let dy = -3; dy <= 3; dy++) for (let dz = -3; dz <= 3; dz++) {
        const b = this.grid.get(`${cx + dx},${cy + dy},${cz + dz}`);
        if (!b) continue;
        for (const j of b) {
          if (nx * nrm[3 * j] + ny * nrm[3 * j + 1] + nz * nrm[3 * j + 2] > -0.3) continue;   // 마주보는 면만
          const vx = pos[3 * j] - x, vy = pos[3 * j + 1] - y, vz = pos[3 * j + 2] - z;
          const along = -(vx * nx + vy * ny + vz * nz);                                        // 법선 반대쪽으로의 거리
          if (along < 0.01) continue;
          const d2 = vx * vx + vy * vy + vz * vz;
          if (d2 > along * along * 2.5) continue;                                              // 법선 방향에서 너무 벗어난 점 제외
          if (along < best) best = along;
        }
      }
      if (Number.isFinite(best)) th[i] = Math.min(best, 0.35);
    }
    return th;
  }

  private innerPositions() {
    const th = this.thick;
    const out = new Float32Array(this.n * 3);
    for (let i = 0; i < this.n; i++) {
      // 두께의 25~70% 지점. 위치 노이즈로 부드럽게 달라져 경로가 안에서 굽이친다
      const u = 0.25 + 0.45 * valueNoise(this.pos[3 * i] * 6 + 3, this.pos[3 * i + 1] * 6, this.pos[3 * i + 2] * 6 - 5);
      const d = th[i] * u;
      this.innerDepth[i] = d;
      out[3 * i] = this.pos[3 * i] - this.nrm[3 * i] * d;
      out[3 * i + 1] = this.pos[3 * i + 1] - this.nrm[3 * i + 1] * d;
      out[3 * i + 2] = this.pos[3 * i + 2] - this.nrm[3 * i + 2] * d;
    }
    return out;
  }

  private cellKey(x: number, y: number, z: number) {
    return `${Math.floor(x / this.cell)},${Math.floor(y / this.cell)},${Math.floor(z / this.cell)}`;
  }

  nearestVertex(v: THREE.Vector3): number {
    const cx = Math.floor(v.x / this.cell), cy = Math.floor(v.y / this.cell), cz = Math.floor(v.z / this.cell);
    let best = -1, bd = Infinity;
    for (let r = 1; r <= 3 && best < 0; r++) {
      for (let dx = -r; dx <= r; dx++) for (let dy = -r; dy <= r; dy++) for (let dz = -r; dz <= r; dz++) {
        const b = this.grid.get(`${cx + dx},${cy + dy},${cz + dz}`);
        if (!b) continue;
        for (const i of b) {
          const d = (this.pos[3 * i] - v.x) ** 2 + (this.pos[3 * i + 1] - v.y) ** 2 + (this.pos[3 * i + 2] - v.z) ** 2;
          if (d < bd) { bd = d; best = i; }
        }
      }
    }
    return best;
  }

  /** 두 정점 사이 최단 경로 (노이즈 섞인 간선 길이 기준이라 살짝 구불거린다). from → to 순서의 정점 목록. 못 가면 []. */
  shortestPath(from: number, to: number): number[] {
    const dist = new Float32Array(this.n).fill(Infinity);
    const parent = new Int32Array(this.n).fill(-1);
    const done = new Uint8Array(this.n);
    const heap = new MinHeap();
    dist[from] = 0;
    heap.push(0, from, 1);
    while (heap.size) {
      const [d, v] = heap.pop();
      if (done[v]) continue;
      done[v] = 1;
      if (v === to) break;
      for (let j = this.adjStart[v]; j < this.adjStart[v + 1]; j++) {
        const u = this.adj[j];
        if (done[u]) continue;
        const nd = d + this.adjW[j];
        if (nd < dist[u]) { dist[u] = nd; parent[u] = v; heap.push(nd, u, 1); }
      }
    }
    if (!done[to]) return [];
    const path: number[] = [];
    for (let v = to; v >= 0; v = parent[v]) path.push(v);
    return path.reverse();
  }

  /** 다익스트라. 정점마다 도착 시간(0~1 정규화)과 부모 정점을 돌려준다. */
  compute(seeds: Seed[]) {
    const time = new Float32Array(this.n).fill(Infinity);
    const parent = new Int32Array(this.n).fill(-1);
    const done = new Uint8Array(this.n);
    const heap = new MinHeap();
    for (const s of seeds) {
      const v = this.nearestVertex(s.pos);
      // 씨앗은 시작부터 시간 0으로 적어 둔다. 안 그러면 이웃 씨앗이 먼저 뽑혀 이 씨앗을 '자식'으로 적어 버린다
      if (v >= 0) { time[v] = 0; heap.push(0, v, s.speed); }
    }
    let tMax = 0;
    while (heap.size) {
      const [t, v, sp] = heap.pop();
      if (done[v]) continue;
      done[v] = 1;
      time[v] = t;
      if (t > tMax) tMax = t;
      for (let j = this.adjStart[v]; j < this.adjStart[v + 1]; j++) {
        const u = this.adj[j];
        if (done[u]) continue;
        const nt = t + this.adjW[j] / sp;
        if (nt < time[u]) { time[u] = nt; parent[u] = v; heap.push(nt, u, sp); }
      }
    }
    const arrival = new Float32Array(this.n);
    const seedVertices: number[] = [];
    for (let i = 0; i < this.n; i++) {
      arrival[i] = Number.isFinite(time[i]) && tMax > 0 ? time[i] / tMax : 1;
      if (done[i] && parent[i] < 0) seedVertices.push(i);
    }
    return { arrival, parent, seedVertices };
  }

  /** 부모 연결로 뿌리 나무를 만들고, 후손 수로 굵기 층을 나눈다.
   *  lift: 법선 방향 오프셋(층마다 다르게 줄 수 있다). "inner"면 손 안쪽 위치(inner)를 쓴다. */
  roots(arrival: Float32Array, parent: Int32Array, lift: number | number[] | "inner" = 0.008, minDesc = 14): RootTier[] {
    // 도착 시간이 늦은 정점부터 부모에게 후손 수를 넘긴다
    const order = Array.from({ length: this.n }, (_, i) => i).sort((a, b) => arrival[b] - arrival[a]);
    const desc = new Float32Array(this.n).fill(1);
    for (const v of order) if (parent[v] >= 0) desc[parent[v]] += desc[v];
    let maxD = 1;
    for (let i = 0; i < this.n; i++) if (parent[i] >= 0 && desc[i] > maxD) maxD = desc[i];
    const logMax = Math.log(maxD);
    // 굵기 층: [하한 무게, 상한 무게)
    const bands: [number, number, number][] = [[0.66, 1.01, 1.0], [0.5, 0.66, 0.6], [0, 0.5, 0.25]];
    const segs: { a: number; b: number; t: number; w: number }[] = [];
    for (let v = 0; v < this.n; v++) {
      const p = parent[v];
      if (p < 0 || desc[v] < minDesc) continue;
      segs.push({ a: p, b: v, t: arrival[v], w: Math.log(desc[v]) / logMax });
    }
    segs.sort((x, y) => x.t - y.t);
    return bands.map(([lo, hi, weight], band) => {
      const mine = segs.filter((s) => s.w >= lo && s.w < hi);
      const off = lift === "inner" ? 0 : Array.isArray(lift) ? lift[Math.min(band, lift.length - 1)] : lift;
      const base = lift === "inner" ? this.inner : this.pos;
      const positions = new Float32Array(mine.length * 6);
      const arrivals = new Float32Array(mine.length);
      mine.forEach((s, k) => {
        for (const [o, vtx] of [[0, s.a], [3, s.b]] as const) {
          positions[6 * k + o] = base[3 * vtx] + this.nrm[3 * vtx] * off;
          positions[6 * k + o + 1] = base[3 * vtx + 1] + this.nrm[3 * vtx + 1] * off;
          positions[6 * k + o + 2] = base[3 * vtx + 2] + this.nrm[3 * vtx + 2] * off;
        }
        arrivals[k] = s.t;
      });
      return { positions, arrivals, weight };
    });
  }
}

export interface SporePath { chain: Int32Array; times: Float32Array; }   // 씨앗 → 끝 정점, 도착 시간 오름차순

/** 포자 경로: 아무 정점에서 부모를 거슬러 씨앗까지 올라간 사슬. 포자는 이 사슬을 따라 씨앗에서 바깥으로 흐른다. */
export function sporePaths(arrival: Float32Array, parent: Int32Array, count: number, minLen = 6): SporePath[] {
  const n = arrival.length;
  const out: SporePath[] = [];
  let guard = 0;
  while (out.length < count && guard++ < count * 4) {
    let v = Math.floor(Math.random() * n);
    const chain: number[] = [];
    while (v >= 0 && chain.length < 400) { chain.push(v); v = parent[v]; }
    if (chain.length < minLen) continue;
    chain.reverse();
    out.push({ chain: Int32Array.from(chain), times: Float32Array.from(chain, (i) => arrival[i]) });
  }
  return out;
}

/** 정렬된 배열에서 value 이하인 원소 개수. */
export function countUpTo(sorted: Float32Array, value: number) {
  let lo = 0, hi = sorted.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (sorted[m] <= value) lo = m + 1; else hi = m; }
  return lo;
}

/** 단계(1~10) → 덮이는 비율 (명세서 4.8절). */
export const COVERAGE = [0, 0.06, 0.13, 0.22, 0.33, 0.46, 0.6, 0.76, 0.9, 1.0];
