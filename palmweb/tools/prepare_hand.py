"""3D 손 모델 가공: WebXR generic-hand(오른손) → 웹에서 쓸 정적 메시 + 관절 위치.

    uv run --group tools python tools/prepare_hand.py

입력  assets/hand/generic-hand-right.glb  (뼈대가 있는 1,360 정점 손)
출력  web/public/models/hand.glb          정적 메시 (세분화, 정규화 좌표)
      web/public/models/hand.joints.json  MediaPipe 21점 순서의 관절 위치

정규화 좌표 (모델 공간)
- 원점: 손바닥 중심 (손목과 네 손가락 뿌리의 평균)
- +y: 손목 → 중지 뿌리 (손가락 방향), 길이 1 = 손목~중지 뿌리 거리
- +x: 검지 뿌리 → 소지 뿌리 쪽 (화면에서 오른쪽)
- +z: 손바닥이 향하는 쪽 (카메라 쪽)
이렇게 두면 손바닥을 정면에서 볼 때 엄지가 왼쪽에 온다. 손금 표준 좌표계(엄지 왼쪽)와 같은 방향이다.
원본 오른손은 손바닥을 정면으로 향하면 엄지가 오른쪽에 오므로, 이 변환은 거울 반전을 포함한다.
웹에서는 정면화 때 좌우를 뒤집은 손(PalmLineSet.mirrored)만 다시 반전해 보여준다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import trimesh
from pygltflib import GLTF2

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "hand" / "generic-hand-right.glb"
OUT_DIR = ROOT / "web" / "public" / "models"
SUBDIV = 2   # Loop 세분화 횟수 (삼각형 4^n 배)

# MediaPipe 21점 → WebXR 관절 이름
MP_TO_XR = [
    "wrist",
    "thumb-metacarpal", "thumb-phalanx-proximal", "thumb-phalanx-distal", "thumb-tip",
    "index-finger-phalanx-proximal", "index-finger-phalanx-intermediate", "index-finger-phalanx-distal", "index-finger-tip",
    "middle-finger-phalanx-proximal", "middle-finger-phalanx-intermediate", "middle-finger-phalanx-distal", "middle-finger-tip",
    "ring-finger-phalanx-proximal", "ring-finger-phalanx-intermediate", "ring-finger-phalanx-distal", "ring-finger-tip",
    "pinky-finger-phalanx-proximal", "pinky-finger-phalanx-intermediate", "pinky-finger-phalanx-distal", "pinky-finger-tip",
]


def _accessor(g: GLTF2, idx: int) -> np.ndarray:
    acc = g.accessors[idx]
    view = g.bufferViews[acc.bufferView]
    blob = g.binary_blob()
    comp = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}[acc.componentType]
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[acc.type]
    start = (view.byteOffset or 0) + (acc.byteOffset or 0)
    stride = view.byteStride or np.dtype(comp).itemsize * n
    raw = np.frombuffer(blob, dtype=np.uint8, count=stride * (acc.count - 1) + np.dtype(comp).itemsize * n, offset=start)
    arr = np.lib.stride_tricks.as_strided(raw.view(comp), shape=(acc.count, n), strides=(stride, np.dtype(comp).itemsize))
    arr = np.array(arr, dtype=np.float64 if comp == np.float32 else np.int64)
    if acc.normalized and comp in (np.uint8, np.uint16):
        arr = arr / np.iinfo(comp).max
    return arr


def _local(n) -> np.ndarray:
    if n.matrix:
        return np.array(n.matrix, dtype=np.float64).reshape(4, 4).T
    T, R, S = np.eye(4), np.eye(4), np.eye(4)
    if n.translation:
        T[:3, 3] = n.translation
    if n.rotation:
        x, y, z, w = n.rotation
        R[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    if n.scale:
        S[:3, :3] = np.diag(n.scale)
    return T @ R @ S


def load_rest_pose(path: Path):
    g = GLTF2().load(str(path))
    parent = {c: i for i, n in enumerate(g.nodes) for c in (n.children or [])}

    def world(i):
        M = _local(g.nodes[i])
        while i in parent:
            i = parent[i]
            M = _local(g.nodes[i]) @ M
        return M

    prim = g.meshes[0].primitives[0]
    pos = _accessor(g, prim.attributes.POSITION)
    faces = _accessor(g, prim.indices).reshape(-1, 3)
    joints = _accessor(g, prim.attributes.JOINTS_0).astype(int)
    weights = _accessor(g, prim.attributes.WEIGHTS_0)
    skin = g.skins[0]
    ibm = _accessor(g, skin.inverseBindMatrices).reshape(-1, 4, 4).transpose(0, 2, 1)
    # 정점 스키닝: v' = Σ w_i · (관절 월드 · 역바인드) · v  (휴식 자세 그대로 굽는다)
    J = np.stack([world(j) @ ibm[k] for k, j in enumerate(skin.joints)])
    vh = np.c_[pos, np.ones(len(pos))]
    skinned = np.zeros_like(vh)
    for k in range(4):
        skinned += weights[:, k:k + 1] * np.einsum("nij,nj->ni", J[joints[:, k]], vh)
    names = {n.name: i for i, n in enumerate(g.nodes)}
    jpos = np.array([world(names[nm])[:3, 3] for nm in MP_TO_XR])
    return skinned[:, :3], faces, jpos


def normalize(verts: np.ndarray, jpos: np.ndarray):
    wrist, idx, mid, rng, pky = jpos[0], jpos[5], jpos[9], jpos[13], jpos[17]
    y = mid - wrist
    scale = np.linalg.norm(y)
    y = y / scale
    x = pky - idx
    x = x - y * (x @ y)
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    # 손가락은 손바닥 쪽으로 살짝 굽는다 → 손끝이 향한 쪽이 손바닥 쪽(+z)이어야 한다
    tips = jpos[[8, 12, 16, 20]].mean(0) - jpos[[5, 9, 13, 17]].mean(0)
    if tips @ z < 0:
        # z를 뒤집으면 오른손 좌표계를 유지하려고 x도 뒤집어야 하는데, 그러면 엄지가 오른쪽에 온다.
        # 대신 모델을 거울 반전한다 (오른손 모델 → 정면에서 엄지가 왼쪽).
        z = -z
    center = np.mean([wrist, idx, mid, rng, pky], axis=0)
    R = np.stack([x, y, z])        # 행: 새 축
    f = lambda p: ((p - center) @ R.T) / scale
    return f(verts), f(jpos), (np.linalg.det(R) < 0)


def main():
    verts, faces, jpos = load_rest_pose(SRC)
    v, j, flipped = normalize(verts, jpos)
    mesh = trimesh.Trimesh(v, faces, process=True)
    if flipped:
        mesh.invert()  # 거울 반전으로 뒤집힌 면 방향을 바로잡는다
    mesh.merge_vertices()
    print(f"원본 정점 {len(mesh.vertices)} 삼각형 {len(mesh.faces)} 반전 {flipped} 닫힘 {mesh.is_watertight}")
    for _ in range(SUBDIV):
        mesh = mesh.subdivide_loop(iterations=1)
    mesh.fix_normals()
    print(f"세분화 후 정점 {len(mesh.vertices)} 삼각형 {len(mesh.faces)}")
    # 손바닥(+z) 쪽이 실제로 바깥을 향하는지 확인: 손바닥 중심에서 +z로 쏜 광선이 맞는 면의 법선
    loc, _, tri = mesh.ray.intersects_location([[0, 0, 2]], [[0, 0, -1]])
    first = int(np.argmax(loc[:, 2])) if len(loc) else None
    if first is None:
        raise SystemExit("손바닥 중심에서 +z 광선이 손에 닿지 않습니다")
    n = mesh.face_normals[tri[first]]
    print(f"손바닥 중심 표면 z={loc[first, 2]:.3f}, 법선 z={n[2]:.2f} (양수면 손바닥이 +z를 향함)")
    if n[2] <= 0:
        raise SystemExit("손바닥 면 법선이 +z를 향하지 않습니다")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mesh.export(OUT_DIR / "hand.glb")
    data = {
        "note": "MediaPipe 21점 순서의 관절 위치. 모델 공간: 원점 손바닥 중심, +y 손가락, +x 소지 쪽, +z 손바닥이 향하는 쪽, 단위 = 손목~중지 뿌리",
        "source": "WebXR generic-hand right.glb (MIT, Amazon)",
        "landmarks": np.round(j, 5).tolist(),
        "bounds": {"min": np.round(mesh.bounds[0], 4).tolist(), "max": np.round(mesh.bounds[1], 4).tolist()},
    }
    (OUT_DIR / "hand.joints.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print("관절(정규화):")
    for k in (0, 1, 2, 4, 5, 9, 13, 17, 12):
        print(f"  {k:2d} {MP_TO_XR[k]:34s} {np.round(j[k], 3)}")
    print("범위", data["bounds"])


if __name__ == "__main__":
    main()
