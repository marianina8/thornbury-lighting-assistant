"""Build demo/thornbury_snoot_showcase.blend: five stations in a row, each a
ceramic vase on a plinth lit by one light wearing a different managed snoot.

Built with the addon installed (so the snoots are the real, driver-scaled
ones), using the Blender 4.2 bpy wheel so the file opens in 4.2 and later:

    BLENDER_USER_RESOURCES=$(mktemp -d) <bpy-4.2 venv>/bin/python tools/make_snoot_showcase.py [--render out.png]
"""
import importlib
import math
import os
import sys

import bpy  # noqa: I001 - bpy must be imported before bmesh/mathutils in the module build
import bmesh
from mathutils import Vector

ROOT = os.environ.get("TLA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "bl_ext.user_default.thornbury_lighting"


def install_addon():
    import re
    manifest = open(os.path.join(ROOT, "addon", "thornbury_lighting", "blender_manifest.toml"), encoding="utf-8").read()
    ver = re.search(r'^version = "([^"]+)"', manifest, re.M).group(1)
    zip_path = os.path.join(ROOT, "dist", "thornbury_lighting-%s.zip" % ver)
    bpy.ops.extensions.package_install_files(filepath=zip_path, repo="user_default", enable_on_install=True)
    return importlib.import_module(PKG + ".snoot")


def material(name, rgb, rough=0.5, emit=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    if emit:
        key = "Emission Color" if "Emission Color" in b.inputs else "Emission"
        b.inputs[key].default_value = (*rgb, 1)
        b.inputs["Emission Strength"].default_value = emit
    m.diffuse_color = (*rgb, 1)
    return m


def link(ob):
    bpy.context.scene.collection.objects.link(ob)
    return ob


def vase(name, loc, mat):
    """A lathed vase: foot, belly, neck and lip (a new subject, not the old cylinder + sphere)."""
    profile = [(0.0, 0.0), (0.16, 0.0), (0.18, 0.03), (0.14, 0.08), (0.2, 0.2), (0.27, 0.36), (0.28, 0.5),
               (0.24, 0.64), (0.15, 0.76), (0.1, 0.86), (0.1, 0.95), (0.14, 1.02), (0.15, 1.05)]
    bm = bmesh.new()
    verts = [bm.verts.new((r, 0.0, z)) for r, z in profile]
    edges = [bm.edges.new((verts[i], verts[i + 1])) for i in range(len(verts) - 1)]
    bmesh.ops.spin(bm, geom=verts + edges, cent=(0, 0, 0), axis=(0, 0, 1), angle=2 * math.pi, steps=48, use_duplicate=False)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(mat)
    ob = link(bpy.data.objects.new(name, me))
    ob.location = loc
    return ob


def box(name, loc, size, mat):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = link(bpy.data.objects.new(name, me))
    ob.location, ob.scale = loc, size
    return ob


def label(text, loc, mat):
    cu = bpy.data.curves.new(text, "FONT")
    cu.body, cu.size, cu.align_x = text, 0.2, "CENTER"
    cu.materials.append(mat)
    ob = link(bpy.data.objects.new("Label: " + text, cu))
    ob.location, ob.rotation_euler = loc, (math.radians(90), 0, 0)
    return ob


def aim(ob, target):
    ob.rotation_euler = (Vector(target) - ob.location).to_track_quat("-Z", "Y").to_euler()


STATIONS = [
    # name, light type, settings, snoot length, mouth, caption
    ("Spot Snoot", "SPOT", {"energy": 450, "spot_size": math.radians(70), "spot_blend": 0.2, "shadow_soft_size": 0.15,
                            "color": (1.0, 0.82, 0.62)}, 1.0, 0.5, "Spot: round snoot"),
    ("Pin Spot", "SPOT", {"energy": 700, "spot_size": math.radians(70), "spot_blend": 0.2, "shadow_soft_size": 0.12,
                          "color": (1.0, 0.9, 0.78)}, 3.0, 0.3, "Spot: long, tight snoot"),
    ("Square Box", "AREA", {"energy": 380, "shape": "SQUARE", "size": 0.4, "color": (0.85, 0.9, 1.0)}, 2.6, 0.5,
     "Square area: box snoot"),
    ("Disk Round", "AREA", {"energy": 380, "shape": "DISK", "size": 0.45, "color": (1.0, 0.95, 0.88)}, 2.0, 0.6,
     "Disk area: round snoot"),
    ("Strip Box", "AREA", {"energy": 420, "shape": "RECTANGLE", "size": 0.7, "size_y": 0.18, "color": (0.95, 1.0, 0.9)},
     2.6, 0.5, "Strip area: slot snoot"),
]


def build(snoot):
    bpy.ops.wm.read_homefile(use_empty=True)
    sc = bpy.context.scene
    sc.name = "Thornbury snoot showcase"
    sc.render.engine = "CYCLES"
    sc.cycles.samples, sc.cycles.use_denoising = 128, True
    sc.render.resolution_x, sc.render.resolution_y = 1920, 820
    vt = [i.identifier for i in sc.view_settings.bl_rna.properties["view_transform"].enum_items]
    sc.view_settings.view_transform = "AgX" if "AgX" in vt else "Filmic"
    world = bpy.data.worlds.new("Dim studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.004, 0.0045, 0.006, 1)
    sc.world = world

    floor_m = material("Studio floor", (0.16, 0.16, 0.17), 0.7)
    wall_m = material("Studio wall", (0.5, 0.49, 0.47), 0.85)
    plinth_m = material("Plinth", (0.08, 0.08, 0.09), 0.4)
    vase_m = material("Glazed ceramic", (0.82, 0.8, 0.76), 0.25)
    label_m = material("Label", (0.9, 0.9, 0.9), 0.5, emit=0.6)

    box("Floor", (0, 0, -0.05), (30, 20, 0.1), floor_m)
    box("Back wall", (0, 3.0, 3), (30, 0.1, 6), wall_m)

    spacing = 3.0
    xs = [spacing * (i - (len(STATIONS) - 1) / 2) for i in range(len(STATIONS))]
    for x, (name, kind, settings, length, mouth, caption) in zip(xs, STATIONS):
        box(name + " plinth", (x, 0.6, 0.4), (0.7, 0.7, 0.8), plinth_m)
        v = vase(name + " vase", (x, 0.6, 0.8), vase_m)
        L = bpy.data.lights.new(name, kind)
        for k, val in settings.items():
            setattr(L, k, val)
        lo = link(bpy.data.objects.new(name, L))
        lo.location = (x - 0.6, -1.4, 3.1)
        aim(lo, (x, 0.6, 1.3))
        snoot.add(lo, length=length, mouth=mouth)
        label(caption, (x, -0.2, 0.02), label_m)
        lo["showcase_note"] = caption

    cam = bpy.data.cameras.new("Camera")
    cam.lens = 26
    co = link(bpy.data.objects.new("Camera", cam))
    co.location = (0, -11.5, 3.0)
    aim(co, (0, 0.8, 1.2))
    sc.camera = co
    first = bpy.data.objects[STATIONS[0][0]]
    bpy.context.view_layer.objects.active = first
    first.select_set(True)
    return sc


def main():
    snoot = install_addon()
    sc = build(snoot)
    out = os.path.join(ROOT, "demo", "thornbury_snoot_showcase.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out, compress=True)
    print("wrote", out, "with Blender", bpy.app.version_string)
    if "--render" in sys.argv:
        path = sys.argv[sys.argv.index("--render") + 1]
        sc.render.resolution_percentage = 50
        sc.render.filepath = path
        bpy.ops.render.render(write_still=True)
        print("rendered", path)


main()
