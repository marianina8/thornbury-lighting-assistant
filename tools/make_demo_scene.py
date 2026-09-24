"""Build demo/thornbury_demo.blend: a stand-in subject in front of a wall, lit
by a warm key spot whose cone spills across the background. It's the scene the
canonical note is written for: "snoot the key down so it stops spilling on the
background, keep it warm".

Run with the Blender 4.2 bpy wheel so the file opens in 4.2 and every later
version:   <bpy-4.2 venv>/bin/python tools/make_demo_scene.py [--render]

--render also writes docs/img/demo-before.png and docs/img/demo-after.png. The
"after" applies a hand-written illustrative proposal (cone 45 -> 26 deg, blend
0.15 -> 0.25), not a model output; replace it with a real one once the backend
is deployed.
"""
import math
import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "addon"))


def aim(ob, target):
    d = Vector(target) - ob.location
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def mat(name, rgb, rough=0.6):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1)
    bsdf.inputs["Roughness"].default_value = rough
    return m


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.name = "Thornbury demo"
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 128
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = 1280, 720
    sc.view_settings.view_transform = "AgX" if "AgX" in [i.identifier for i in sc.view_settings.bl_rna.properties["view_transform"].enum_items] else "Filmic"

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.02, 0.022, 0.028, 1)
    sc.world = world

    grey, wall_m, skin = mat("Floor", (0.18, 0.18, 0.18)), mat("Wall", (0.55, 0.55, 0.52), 0.8), mat("Subject", (0.6, 0.5, 0.45), 0.5)
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    bpy.context.object.name = "Floor"
    bpy.context.object.data.materials.append(grey)
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 4, 5), rotation=(math.radians(90), 0, 0))
    bpy.context.object.name = "Background wall"
    bpy.context.object.data.materials.append(wall_m)
    bpy.ops.mesh.primitive_cylinder_add(radius=0.28, depth=1.2, location=(0, 0, 0.6))
    bpy.context.object.name = "Subject body"
    bpy.context.object.data.materials.append(skin)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.22, location=(0, 0, 1.45), segments=48, ring_count=24)
    bpy.context.object.name = "Subject head"
    bpy.context.object.data.materials.append(skin)
    bpy.ops.object.shade_smooth()

    cam = bpy.data.cameras.new("Camera")
    cam.lens = 40
    co = bpy.data.objects.new("Camera", cam)
    co.location = (0.4, -5.2, 1.5)
    sc.collection.objects.link(co)
    aim(co, (0, 1.0, 1.1))
    sc.camera = co

    key = bpy.data.lights.new("Key", "SPOT")
    key.energy, key.spot_size, key.spot_blend = 900, math.radians(45), 0.15
    key.color, key.shadow_soft_size = (1.0, 0.78, 0.55), 0.15
    ko = bpy.data.objects.new("Key", key)
    ko.location = (-2.2, -2.4, 3.0)
    sc.collection.objects.link(ko)
    aim(ko, (0, 0, 1.2))

    rim = bpy.data.lights.new("Rim", "SPOT")
    rim.energy, rim.spot_size, rim.spot_blend, rim.color = 400, math.radians(30), 0.3, (0.7, 0.8, 1.0)
    ro = bpy.data.objects.new("Rim", rim)
    ro.location = (2.0, 2.2, 2.8)
    sc.collection.objects.link(ro)
    aim(ro, (0, 0, 1.3))

    bpy.context.view_layer.objects.active = ko
    ko.select_set(True)
    return sc, key


def main():
    sc, key = build()
    out = os.path.join(ROOT, "demo", "thornbury_demo.blend")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out, compress=True)
    print("wrote", out, "with Blender", bpy.app.version_string)
    if "--render" in sys.argv:
        img = os.path.join(ROOT, "docs", "img")
        os.makedirs(img, exist_ok=True)
        sc.render.resolution_percentage = 50
        sc.render.filepath = os.path.join(img, "demo-before.png")
        bpy.ops.render.render(write_still=True)
        from thornbury_lighting import lightstate
        lightstate.apply_values(key, {"spot_size": math.radians(26), "spot_blend": 0.25})
        sc.render.filepath = os.path.join(img, "demo-after.png")
        bpy.ops.render.render(write_still=True)
        print("rendered before/after to", img)


main()
