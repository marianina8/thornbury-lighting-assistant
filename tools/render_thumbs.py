"""Render the picker thumbnails: every gobo and beam profile projected onto a
wall by a spot light, through the addon's own node wiring, in Cycles.

    <bpy venv>/bin/python tools/render_thumbs.py

Writes addon/thornbury_lighting/presets/thumbs/<id>.png (plus none.png) and
docs/img/gobo-library.png (a labelled contact sheet).
"""
import json
import math
import os
import sys

import bpy

ROOT = os.environ.get("TLA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "addon"))
from thornbury_lighting import lightstate  # noqa: E402

THUMB = 160
OUT = os.path.join(ROOT, "addon", "thornbury_lighting", "presets", "thumbs")


def scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device, sc.cycles.samples, sc.cycles.use_denoising = "CPU", 48, True
    sc.render.resolution_x = sc.render.resolution_y = THUMB
    sc.render.image_settings.file_format = "PNG"
    sc.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.004, 0.004, 0.005, 1)
    sc.world = world
    mat = bpy.data.materials.new("wall")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.62, 0.6, 0.57, 1)
    bsdf.inputs["Roughness"].default_value = 0.9
    me = bpy.data.meshes.new("wall")
    me.from_pydata([(-4, 0, -4), (4, 0, -4), (4, 0, 4), (-4, 0, 4)], [], [(0, 3, 2, 1)])
    me.materials.append(mat)
    sc.collection.objects.link(bpy.data.objects.new("wall", me))
    cam = bpy.data.cameras.new("cam")
    cam.type, cam.ortho_scale = "ORTHO", 3.8
    co = bpy.data.objects.new("cam", cam)
    co.location, co.rotation_euler = (0, -6, 0), (math.radians(90), 0, 0)
    sc.collection.objects.link(co)
    sc.camera = co
    light = bpy.data.lights.new("Key", "SPOT")
    light.energy, light.spot_size, light.spot_blend, light.shadow_soft_size = 260, math.radians(60), 0.04, 0.0
    light.color = (1.0, 0.94, 0.86)
    lo = bpy.data.objects.new("Key", light)
    lo.location, lo.rotation_euler = (0, -3, 0), (math.radians(90), 0, 0)
    sc.collection.objects.link(lo)
    return sc, light


def main():
    os.makedirs(OUT, exist_ok=True)
    sc, light = scene()
    ids = ["none"] + list(lightstate.LIBRARY)
    for pid in ids:
        lightstate.set_preset(light, pid)
        sc.render.filepath = os.path.join(OUT, pid + ".png")
        bpy.ops.render.render(write_still=True)
    lightstate.set_preset(light, "none")
    print("rendered %d thumbnails to %s" % (len(ids), OUT))
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    lib = json.load(open(os.path.join(ROOT, "internal", "presets", "presets.json")))["presets"]
    cols = 7
    rows = (len(lib) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * THUMB, rows * (THUMB + 20)), (16, 16, 16))
    d = ImageDraw.Draw(sheet)
    for i, p in enumerate(lib):
        x, y = (i % cols) * THUMB, (i // cols) * (THUMB + 20)
        sheet.paste(Image.open(os.path.join(OUT, p["id"] + ".png")).convert("RGB"), (x, y))
        d.text((x + 5, y + THUMB + 4), p["label"], fill=(225, 225, 225))
    path = os.path.join(ROOT, "docs", "img", "gobo-library.png")
    sheet.save(path)
    print("wrote", path)


main()
