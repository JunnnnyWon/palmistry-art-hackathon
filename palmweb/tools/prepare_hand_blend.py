"""Blender 손(실사 텍스처) + 모델링한 핏줄 → 웹용 메시·관절·핏줄·텍스처.

1) Blender 안에서 (tools/blend_export.py):  손목 구멍 메우기, 캣멀클락 2회 세분화, UV 포함 OBJ, 핏줄 커브 샘플링 JSON
2) 이 스크립트:
    uv run --group tools python tools/prepare_hand_blend.py <obj> <veins_raw.json> <textures_dir>

    출력  web/public/models/hand.glb          정적 메시 (정규화 좌표, UV 포함)
          web/public/models/hand.joints.json  MediaPipe 21점 순서의 관절 위치 (메시 형상에서 추정)
          web/public/models/veins.json        핏줄 중심선 [x, y, z, 반지름] 폴리라인 (정규화 좌표, 호 길이로 재샘플)
          web/public/textures/hand_*.{jpg,png} 알베도 · 노멀 · 거칠기/금속(three.js 채널 순서)

정규화 좌표 (모델 공간, prepare_hand.py 와 같다)
- 원점: 손바닥 중심 (손목과 네 손가락 뿌리의 평균)
- +y: 손목 → 중지 뿌리 (손가락 방향), 길이 1 = 손목~중지 뿌리 거리
- +x: 검지 뿌리 → 소지 뿌리 쪽 (화면에서 오른쪽)
- +z: 손바닥이 향하는 쪽 (카메라 쪽)
Blender 파일의 손은 +z 손가락, +y 손바닥, -x 엄지라서 이 변환은 거울 반전을 포함한다 (엄지가 왼쪽에 오도록).

관절 추정: 손가락마다 x 띠를 정해 끝(가장 높은 점)과 갈래(인접 손가락 사이 웨빙의 가장 낮은 점)를 찾고,
MCP 관절 = 갈래보다 손가락 길이의 22% 아래, PIP·DIP = MCP→끝의 44%·74% 지점의 손가락 중심선. 엄지는 손목→엄지 끝 축 위.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
OUT_MODELS = ROOT / "web" / "public" / "models"
OUT_TEX = ROOT / "web" / "public" / "textures"

# Blender 좌표에서 손가락 x 띠 (세분화 메시를 z 슬라이스로 훑어 정한 값)
BANDS = {"index": (-1.45, -0.68), "middle": (-0.62, 0.08), "ring": (0.10, 0.72), "pinky": (0.75, 1.30)}
GAPS = {("index", "middle"): -0.63, ("middle", "ring"): 0.09, ("ring", "pinky"): 0.73}
WRIST_Z = 0.15
MP_NAMES = ["wrist", "thumb-cmc", "thumb-mcp", "thumb-ip", "thumb-tip",
            "index-mcp", "index-pip", "index-dip", "index-tip", "middle-mcp", "middle-pip", "middle-dip", "middle-tip",
            "ring-mcp", "ring-pip", "ring-dip", "ring-tip", "pinky-mcp", "pinky-pip", "pinky-dip", "pinky-tip"]


def load_obj(path: Path):
    """UV 포함 OBJ → 분할 정점(위치+UV 쌍마다 하나), 면, 원래 위치 정점 id."""
    pos, uvs, faces = [], [], []
    for line in open(path):
        if line.startswith("v "):
            pos.append([float(x) for x in line.split()[1:4]])
        elif line.startswith("vt "):
            uvs.append([float(x) for x in line.split()[1:3]])
        elif line.startswith("f "):
            faces.append([tuple(int(i) - 1 for i in tok.split("/")[:2]) for tok in line.split()[1:4]])
    pos, uvs = np.array(pos), np.array(uvs)
    key = {}
    sv, suv, sorig = [], [], []
    out_faces = []
    for tri in faces:
        ids = []
        for (vi, ti) in tri:
            k = (vi, round(uvs[ti][0], 5), round(uvs[ti][1], 5))   # 같은 정점·같은 UV면 하나로 (OBJ는 코너마다 vt를 썼다)
            if k not in key:
                key[k] = len(sv)
                sv.append(pos[vi]); suv.append(uvs[ti]); sorig.append(vi)
            ids.append(key[k])
        out_faces.append(ids)
    return np.array(sv), np.array(suv), np.array(out_faces), np.array(sorig), pos


def estimate_joints(P: np.ndarray) -> np.ndarray:
    """Blender 좌표의 정점 구름에서 MediaPipe 21 관절 추정."""
    J = np.zeros((21, 3))

    def centerline(xlo, xhi, z, dz=0.08):
        sel = P[(P[:, 0] > xlo) & (P[:, 0] < xhi) & (np.abs(P[:, 2] - z) < dz)]
        return sel.mean(0) if len(sel) else None

    # 손목: 슬라이스 중심
    sl = P[np.abs(P[:, 2] - WRIST_Z) < 0.08]
    J[0] = sl.mean(0)
    tips, valleys = {}, {}
    for name, (lo, hi) in BANDS.items():
        band = P[(P[:, 0] > lo) & (P[:, 0] < hi) & (P[:, 2] > 3.0)]
        top = band[band[:, 2] >= np.percentile(band[:, 2], 99)]
        tips[name] = top.mean(0)
    for (a, b), gx in GAPS.items():
        # 인접 손가락 사이 세로 띠: 웨빙 아래는 손바닥 면으로 차 있고 위는 비어 있다 → 띠에서 가장 높은 점이 갈래
        gap = P[(np.abs(P[:, 0] - gx) < 0.04) & (P[:, 2] > 2.0)]
        valleys[(a, b)] = gap[:, 2].max()
    vz = {
        "index": valleys[("index", "middle")],
        "middle": (valleys[("index", "middle")] + valleys[("middle", "ring")]) / 2,
        "ring": (valleys[("middle", "ring")] + valleys[("ring", "pinky")]) / 2,
        "pinky": valleys[("ring", "pinky")],
    }
    base = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}
    for name, (lo, hi) in BANDS.items():
        tip = tips[name]
        z_mcp = vz[name] - 0.22 * (tip[2] - vz[name])
        mcp = centerline(lo, hi, z_mcp, 0.1)
        k = base[name]
        J[k] = mcp
        for off, frac in ((1, 0.44), (2, 0.74)):
            z = z_mcp + frac * (tip[2] - z_mcp)
            c = centerline(lo, hi, z)
            J[k + off] = c if c is not None else mcp + (tip - mcp) * frac
        J[k + 3] = tip
    # 엄지: x < -2.0 인 점 중 손목에서 가장 먼 1% 평균이 끝
    th = P[P[:, 0] < -2.0]
    d = np.linalg.norm(th - J[0], axis=1)
    J[4] = th[d >= np.percentile(d, 99)].mean(0)
    axis = J[4] - J[0]
    L = np.linalg.norm(axis); axis /= L
    thumb = P[P[:, 0] < -1.75]
    for k, frac in ((1, 0.28), (2, 0.55), (3, 0.80)):
        target = J[0] + axis * (L * frac)
        if k == 1:
            J[k] = target
            J[k][1] = sl[:, 1].mean()   # 손바닥 두께 가운데
        else:
            t = (thumb - J[0]) @ axis
            sel = thumb[np.abs(t - L * frac) < 0.12]
            J[k] = sel.mean(0) if len(sel) else target
    return J


def normalize(verts: np.ndarray, jpos: np.ndarray):
    """prepare_hand.py 와 같은 정규화. (x, y, z) 축 행렬 R 과 배율·중심을 돌려준다."""
    wrist, idx, mid, rng, pky = jpos[0], jpos[5], jpos[9], jpos[13], jpos[17]
    y = mid - wrist
    scale = np.linalg.norm(y)
    y = y / scale
    x = pky - idx
    x = x - y * (x @ y)
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    tips = jpos[[8, 12, 16, 20]].mean(0) - jpos[[5, 9, 13, 17]].mean(0)
    if tips @ z < 0:
        z = -z   # 거울 반전 (엄지가 왼쪽에 오도록)
    center = np.mean([wrist, idx, mid, rng, pky], axis=0)
    R = np.stack([x, y, z])
    f = lambda p: ((np.asarray(p) - center) @ R.T) / scale
    return f, scale, (np.linalg.det(R) < 0)


def resample(pts: np.ndarray, spacing: float) -> np.ndarray:
    """[x,y,z,r] 폴리라인을 호 길이 간격으로 다시 샘플 (양 끝 유지)."""
    if len(pts) < 2:
        return pts
    seg = np.linalg.norm(np.diff(pts[:, :3], axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg)])
    total = cum[-1]
    n = max(2, int(round(total / spacing)) + 1)
    s = np.linspace(0, total, n)
    out = np.stack([np.interp(s, cum, pts[:, k]) for k in range(4)], axis=1)
    return out


def main():
    obj_path, veins_path, tex_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    sv, suv, faces, sorig, pos = load_obj(obj_path)
    J = estimate_joints(pos)
    f, scale, flipped = normalize(sv, J)
    v = f(sv)
    j = f(J)
    mesh = trimesh.Trimesh(v, faces, process=False, visual=trimesh.visual.TextureVisuals(uv=suv))
    if flipped:
        mesh.invert()
    mesh.fix_normals()
    # 손바닥(+z)이 바깥을 향하는지 확인
    loc, _, tri = mesh.ray.intersects_location([[0, 0, 3]], [[0, 0, -1]])
    first = int(np.argmax(loc[:, 2])) if len(loc) else None
    if first is None:
        raise SystemExit("손바닥 중심에서 +z 광선이 손에 닿지 않습니다")
    n = mesh.face_normals[tri[first]]
    print(f"정점 {len(mesh.vertices)} 삼각형 {len(mesh.faces)} 반전 {flipped} 배율 1/{scale:.3f} 손바닥 표면 z={loc[first, 2]:.3f} 법선 z={n[2]:.2f}")
    if n[2] <= 0:
        raise SystemExit("손바닥 면 법선이 +z를 향하지 않습니다")
    OUT_MODELS.mkdir(parents=True, exist_ok=True)
    mesh.export(OUT_MODELS / "hand.glb")
    data = {
        "note": "MediaPipe 21점 순서의 관절 위치 (메시 형상에서 추정). 모델 공간: 원점 손바닥 중심, +y 손가락, +x 소지 쪽, +z 손바닥이 향하는 쪽, 단위 = 손목~중지 뿌리",
        "source": "Hand_Veins_Natural_v2.blend (lowpoly hand + 모델링한 핏줄)",
        "landmarks": np.round(j, 5).tolist(),
        "bounds": {"min": np.round(mesh.bounds[0], 4).tolist(), "max": np.round(mesh.bounds[1], 4).tolist()},
    }
    (OUT_MODELS / "hand.joints.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print("관절(정규화):")
    for k in (0, 1, 2, 4, 5, 8, 9, 12, 13, 16, 17, 20):
        print(f"  {k:2d} {MP_NAMES[k]:12s} {np.round(j[k], 3)}")
    print("범위", data["bounds"])
    # 확인용 그림: 정점 점구름(x, y) 위에 관절 번호
    img = np.full((1100, 900, 3), 20, np.uint8)
    to_px = lambda q: (int(450 + q[0] * 190), int(850 - q[1] * 190))
    for q in v[::3]:
        px, py = to_px(q)
        if 0 <= px < 900 and 0 <= py < 1100: img[py, px] = (90, 90, 90)
    bones = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]
    for a, b in bones:
        cv2.line(img, to_px(j[a]), to_px(j[b]), (80, 200, 80), 2)
    for k, q in enumerate(j):
        cv2.circle(img, to_px(q), 6, (60, 60, 255) if k in (0, 1, 2, 5, 9, 13, 17) else (255, 200, 60), -1)
        cv2.putText(img, str(k), (to_px(q)[0] + 8, to_px(q)[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1)
    cv2.imwrite(str(Path(sys.argv[2]).parent / "joints_check.png"), img)

    # 핏줄: 같은 변환, 반지름은 배율로 나눔, 호 길이 재샘플
    raw = json.load(open(veins_path))
    tier_of = {"01  Asymmetric venous trunks": 0, "02  Oblique digital tributaries": 1, "03  Irregular small tributaries": 2}
    spacing = [0.012, 0.015, 0.02]
    veins = []
    for vn in raw["veins"]:
        t = tier_of.get(vn["collection"], 2)
        pts = np.array(vn["points"], dtype=float)
        if len(pts) < 2:
            continue
        xyz = f(pts[:, :3])
        r = pts[:, 3:4] / scale
        pl = resample(np.concatenate([xyz, r], axis=1), spacing[t])
        veins.append({"t": t, "n": vn["name"], "p": np.round(pl, 4).ravel().tolist()})
    counts = [sum(len(x["p"]) // 4 for x in veins if x["t"] == t) for t in range(3)]
    lens = [[float(np.linalg.norm(np.diff(np.array(x["p"]).reshape(-1, 4)[:, :3], axis=0), axis=1).sum()) for x in veins if x["t"] == t] for t in range(3)]
    print("핏줄 길이 중앙값·합", [(round(float(np.median(L)), 3), round(float(np.sum(L)), 1)) for L in lens])
    (OUT_MODELS / "veins.json").write_text(json.dumps({"note": "핏줄 중심선. p = [x, y, z, 반지름]... (정규화 좌표). t: 0 줄기, 1 손가락 가지, 2 잔가지", "veins": veins}, ensure_ascii=False))
    print(f"핏줄 {len(veins)}개, 점 {counts} (줄기·가지·잔가지), 반지름 중앙값",
          [round(float(np.median([x['p'][3::4] for x in veins if x['t'] == t][0] if False else np.concatenate([np.array(x['p'][3::4]) for x in veins if x['t'] == t]))), 4) for t in range(3)])

    # 텍스처: 알베도 JPG, 노멀 PNG, 금속/스무스니스(Unity: RGB 금속, A 스무스니스) → three.js (G 거칠기, B 금속) PNG
    OUT_TEX.mkdir(parents=True, exist_ok=True)
    albedo = cv2.imread(str(tex_dir / "lowpoly_hand_AlbedoTransparency.png"), cv2.IMREAD_COLOR)
    cv2.imwrite(str(OUT_TEX / "hand_albedo.jpg"), albedo, [cv2.IMWRITE_JPEG_QUALITY, 92])
    normal = cv2.imread(str(tex_dir / "lowpoly_hand_Normal.png"), cv2.IMREAD_COLOR)
    cv2.imwrite(str(OUT_TEX / "hand_normal.png"), normal, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    ms = cv2.imread(str(tex_dir / "lowpoly_hand_MetallicSmoothness.png"), cv2.IMREAD_UNCHANGED)
    metallic = ms[:, :, 2] if ms.ndim == 3 else ms   # BGR(A) → R 채널
    smooth = ms[:, :, 3] if (ms.ndim == 3 and ms.shape[2] == 4) else np.full_like(metallic, 128)
    rm = np.zeros((ms.shape[0], ms.shape[1], 3), dtype=np.uint8)
    rm[:, :, 1] = 255 - smooth   # G = 거칠기
    rm[:, :, 0] = metallic       # B = 금속 (OpenCV는 BGR)
    rm = cv2.resize(rm, (1024, 1024), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT_TEX / "hand_roughmetal.png"), rm, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    print("텍스처 저장: 거칠기 평균", round(float(rm[:, :, 1].mean()) / 255, 3), "금속 평균", round(float(rm[:, :, 0].mean()) / 255, 3))


if __name__ == "__main__":
    main()
