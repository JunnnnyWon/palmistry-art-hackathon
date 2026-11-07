"""Blender 안에서 실행: 손 메시(구멍 메움·캣멀클락 2회·UV 포함 OBJ)와 핏줄 커브 샘플 JSON을 내보낸다.

    S=<출력 폴더> /Applications/Blender.app/Contents/MacOS/Blender --background Hand_Veins_Natural_v2.blend --python tools/blend_export.py
    S=<출력 폴더> /Applications/Blender.app/Contents/MacOS/Blender --background Hand_Veins_Natural_v2.blend --python tools/blend_export_veins.py
    uv run --group tools python tools/prepare_hand_blend.py <S>/hand_subdiv_uv.obj <S>/veins_raw.json <텍스처 폴더>

TEX 는 UV 확인 렌더에 쓰는 알베도 텍스처 폴더. uvcheck_palm.png / uvcheck_back.png 로 텍스처가 제대로 입혀지는지 본다.
"""
import bpy, os, json, bmesh
from mathutils import Vector, geometry
S = os.environ["S"]; TEX = "/Users/junnnny/Downloads/hand/textures"
hand = bpy.data.objects["Hand • original surface"]
me0 = hand.data
# 1) 손목 끝의 구멍(경계 루프 1개, 간선 16개)을 메운다. 새 면의 UV는 루프 UV의 평균
bm = bmesh.new(); bm.from_mesh(me0)
uvl = bm.loops.layers.uv.active
boundary = [e for e in bm.edges if e.is_boundary]
avg = Vector((0.0, 0.0)); cnt = 0
for e in boundary:
    for v in e.verts:
        for l in v.link_loops: avg += l[uvl].uv; cnt += 1
avg /= max(cnt, 1)
res = bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
for f in res["faces"]:
    for l in f.loops: l[uvl].uv = avg
bm.to_mesh(me0); bm.free()
print("CAP faces", len(res["faces"]))
# 2) UV ↔ 텍스처 대응 확인용 렌더: 알베도를 입힌 손을 파일의 카메라로
mat = bpy.data.materials.new("uvcheck"); mat.use_nodes = True
nt = mat.node_tree; bsdf = nt.nodes.get("Principled BSDF")
tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = bpy.data.images.load(os.path.join(TEX, "lowpoly_hand_AlbedoTransparency.png"))
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
hand.data.materials.clear(); hand.data.materials.append(mat)
for o in bpy.data.objects:
    if o.type == "CURVE": o.hide_render = True
scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"; scene.display.shading.light = "STUDIO"; scene.display.shading.color_type = "TEXTURE"
scene.render.resolution_x = scene.render.resolution_y = 700; scene.render.resolution_percentage = 100
scene.render.filepath = os.path.join(S, "uvcheck_palm.png"); bpy.ops.render.render(write_still=True)
# 손등 쪽에서도 한 장 (카메라를 반대편으로)
cam = scene.camera; cam.location.y = -cam.location.y; cam.rotation_euler.z = 0.0
scene.render.filepath = os.path.join(S, "uvcheck_back.png"); bpy.ops.render.render(write_still=True)
# 3) 캣멀클락 2회 세분화 → UV 포함 OBJ (월드 좌표, 삼각형)
mod = hand.modifiers.new("subd", "SUBSURF"); mod.levels = 2; mod.render_levels = 2; mod.subdivision_type = "CATMULL_CLARK"
dg = bpy.context.evaluated_depsgraph_get(); ev = hand.evaluated_get(dg); me = ev.to_mesh(); me.calc_loop_triangles()
uv = me.uv_layers.active.data
with open(os.path.join(S, "hand_subdiv_uv.obj"), "w") as f:
    for v in me.vertices:
        p = ev.matrix_world @ v.co; f.write(f"v {p.x:.6f} {p.y:.6f} {p.z:.6f}\n")
    for l in me.loops:
        u = uv[l.index].uv; f.write(f"vt {u.x:.6f} {u.y:.6f}\n")
    for t in me.loop_triangles:
        f.write("f " + " ".join(f"{t.vertices[k]+1}/{t.loops[k]+1}" for k in range(3)) + "\n")
print("SUBDIV_UV_OK", len(me.vertices), len(me.loop_triangles))
