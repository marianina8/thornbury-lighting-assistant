"""Render the stills for the marian.online demo reel: one spot light on a vase
and a wall, first bare, then snooted, then through a few gallery gobos. Every
state is set with the addon's own code (snoot.add, lightstate.set_preset).

    BLENDER_USER_RESOURCES=$(mktemp -d) <bpy-4.2 venv>/bin/python tools/render_reel.py OUTDIR
"""
import importlib
import math
import os
import re
import sys

import bpy  # noqa: I001 - bpy before bmesh
import bmesh
from mathutils import Vector

ROOT = os.environ.get("TLA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "bl_ext.user_default.thornbury_lighting"
OUT = sys.argv[-1]
W, H, SAMPLES = int(os.environ.get("W", 1280)), int(os.environ.get("H", 800)), int(os.environ.get("SAMPLES", 64))


def install():
    manifest = open(os.path.join(ROOT, "addon", "thornbury_lighting", "blender_manifest.toml"), encoding="utf-8").read()
    ver = re.search(r'^version = "([^"]+)"', manifest, re.M).group(1)
    bpy.ops.extensions.package_install_files(filepath=os.path.join(ROOT, "dist", "thornbury_lighting-%s.zip" % ver),
                                             repo="user_default", enable_on_install=True)
    return importlib.import_module(PKG + ".snoot"), importlib.import_module(PKG + ".lightstate")


def mat(name, rgb, rough):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    return m


def box(name, loc, size, m):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(m)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location, ob.scale = loc, size
    return ob


def vase(loc, m):
    prof = [(0.0, 0.0), (0.16, 0.0), (0.18, 0.03), (0.14, 0.08), (0.2, 0.2), (0.27, 0.36), (0.28, 0.5),
            (0.24, 0.64), (0.15, 0.76), (0.1, 0.86), (0.1, 0.95), (0.14, 1.02), (0.15, 1.05)]
    bm = bmesh.new()
    vs = [bm.verts.new((r, 0, z)) for r, z in prof]
    es = [bm.edges.new((vs[i], vs[i + 1])) for i in range(len(vs) - 1)]
    bmesh.ops.spin(bm, geom=vs + es, cent=(0, 0, 0), axis=(0, 0, 1), angle=2 * math.pi, steps=48)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    me = bpy.data.meshes.new("vase")
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(m)
    ob = bpy.data.objects.new("vase", me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location, ob.scale = loc, (1.4, 1.4, 1.4)


def aim(ob, target):
    ob.rotation_euler = (Vector(target) - ob.location).to_track_quat("-Z", "Y").to_euler()


def main():
    snoot, lightstate = install()
    bpy.ops.wm.read_homefile(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device, sc.cycles.samples, sc.cycles.use_denoising = "CPU", SAMPLES, True
    sc.render.resolution_x, sc.render.resolution_y = W, H
    vt = [i.identifier for i in sc.view_settings.bl_rna.properties["view_transform"].enum_items]
    sc.view_settings.view_transform = "AgX" if "AgX" in vt else "Filmic"
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.006, 0.0065, 0.008, 1)
    sc.world = world
    box("floor", (0, 0, -0.05), (30, 20, 0.1), mat("floor", (0.18, 0.17, 0.17), 0.7))
    box("wall", (0, 1.6, 3), (30, 0.1, 6), mat("wall", (0.55, 0.53, 0.5), 0.85))
    box("plinth", (0, 0.6, 0.45), (0.8, 0.8, 0.9), mat("plinth", (0.07, 0.07, 0.08), 0.4))
    vase((0, 0.6, 0.9), mat("glaze", (0.8, 0.52, 0.36), 0.3))
    L = bpy.data.lights.new("Key", "SPOT")
    L.energy, L.spot_size, L.spot_blend, L.shadow_soft_size = 1100, math.radians(62), 0.15, 0.08
    L.color = (1.0, 0.88, 0.72)
    lo = bpy.data.objects.new("Key", L)
    sc.collection.objects.link(lo)
    lo.location = (-1.3, -3.4, 3.0)
    aim(lo, (0, 1.2, 1.3))
    fill = bpy.data.lights.new("Fill", "AREA")
    fill.energy, fill.size, fill.color = 25, 3, (0.7, 0.8, 1.0)
    fo = bpy.data.objects.new("Fill", fill)
    sc.collection.objects.link(fo)
    fo.location = (3, -4, 2)
    aim(fo, (0, 0.6, 1))
    cam = bpy.data.cameras.new("cam")
    cam.lens = 32
    co = bpy.data.objects.new("cam", cam)
    sc.collection.objects.link(co)
    co.location = (0.6, -6.2, 1.9)
    aim(co, (0, 0.8, 1.35))
    sc.camera = co

    os.makedirs(OUT, exist_ok=True)

    def shot(name):
        sc.render.filepath = os.path.join(OUT, name + ".png")
        bpy.ops.render.render(write_still=True)
        print("rendered", name, flush=True)

    shot("01_bare")
    s = snoot.add(lo, length=1.6, mouth=0.45)
    shot("02_snoot")
    snoot.remove(lo)
    for i, pid in enumerate(["gobo_window_blinds", "gobo_leaf_breakup", "gobo_window_arched", "gobo_water_caustic",
                             "gobo_dots"]):
        lightstate.set_preset(L, pid)
        shot("%02d_%s" % (i + 3, pid))


main()
