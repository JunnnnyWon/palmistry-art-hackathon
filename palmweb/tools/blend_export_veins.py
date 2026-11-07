"""Blender 안에서 실행: 핏줄 커브(베지어)를 세그먼트당 8점으로 샘플해 veins_raw.json 으로 (점마다 [x, y, z, 반지름 = 점 반지름 × bevel_depth]).

    S=<출력 폴더> /Applications/Blender.app/Contents/MacOS/Blender --background Hand_Veins_Natural_v2.blend --python tools/blend_export_veins.py
"""
import bpy, json, os
from mathutils import Vector, geometry
S = os.environ["S"]
hand = bpy.data.objects["Hand • original surface"]
me = hand.data
# 3) 핏줄 중심선 샘플링 (베지어 보간 + 반지름 선형 보간)
veins = []
for o in bpy.data.objects:
    if o.type != "CURVE": continue
    cu = o.data
    coll = o.users_collection[0].name if o.users_collection else ""
    for s in cu.splines:
        pts = []
        if s.type == "BEZIER":
            bp = s.bezier_points
            n = len(bp)
            segs = n if s.use_cyclic_u else n - 1
            for i in range(segs):
                a, b = bp[i], bp[(i + 1) % n]
                res = 8
                sampled = geometry.interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, res + 1)
                for k, p in enumerate(sampled):
                    if k == res and i != segs - 1: break   # 다음 세그먼트의 첫 점과 중복
                    t = k / res
                    r = a.radius * (1 - t) + b.radius * t
                    w = o.matrix_world @ p
                    pts.append([round(w.x, 4), round(w.y, 4), round(w.z, 4), round(r * cu.bevel_depth, 5)])
        else:
            for p in s.points:
                w = o.matrix_world @ Vector(p.co[:3])
                pts.append([round(w.x, 4), round(w.y, 4), round(w.z, 4), round(p.radius * cu.bevel_depth, 5)])
        veins.append({"name": o.name, "collection": coll, "material": o.material_slots[0].material.name if o.material_slots and o.material_slots[0].material else None, "points": pts})
json.dump({"veins": veins, "hand_verts": len(me.vertices)}, open(os.path.join(S, "veins_raw.json"), "w"))
print("EXPORT_OK", len(veins), sum(len(v["points"]) for v in veins))
