"""Render check: a gobo shows in EEVEE (through the gobo card) and in Cycles
(through the light's own texture), and Cycles never gets it twice.

    BLENDER_USER_RESOURCES=$(mktemp -d) <bpy venv>/bin/python tools/render_gobo_engines.py

Scene: a spot light 4 m from a grey wall, radius 3 cm. For each engine it
renders no gobo and the Window blinds gobo, and measures stripe contrast: the
spread of row-average brightness inside the pool (blinds are horizontal
bars). Exits non-zero if a gobo doesn't show, or if the no-gobo pool is striped.
"""
import importlib
import math
import os
import re
import sys

import bpy  # noqa: I001

ROOT = os.environ.get("TLA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "bl_ext.user_default.thornbury_lighting"


def install():
    manifest = open(os.path.join(ROOT, "addon", "thornbury_lighting", "blender_manifest.toml"), encoding="utf-8").read()
    ver = re.search(r'^version = "([^"]+)"', manifest, re.M).group(1)
    bpy.ops.extensions.package_install_files(filepath=os.path.join(ROOT, "dist", "thornbury_lighting-%s.zip" % ver),
                                             repo="user_default", enable_on_install=True)
    return importlib.import_module(PKG + ".gobocard")


def scene():
    bpy.ops.wm.read_homefile(use_empty=True)
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y = 200, 200
    w = bpy.data.worlds.new("w")
    sc.world = w
    me = bpy.data.meshes.new("wall")
    me.from_pydata([(-4, 0, -4), (4, 0, -4), (4, 0, 4), (-4, 0, 4)], [], [(0, 1, 2, 3)])
    wall = bpy.data.objects.new("wall", me)
    sc.collection.objects.link(wall)
    wall.location = (0, 4, 0)
    L = bpy.data.lights.new("Key", "SPOT")
    L.energy, L.spot_size, L.spot_blend, L.shadow_soft_size = 900, math.radians(40), 0.05, 0.03
    lo = bpy.data.objects.new("Key", L)
    sc.collection.objects.link(lo)
    lo.rotation_euler = (math.radians(90), 0, 0)  # shine along +Y at the wall
    cam = bpy.data.cameras.new("cam")
    cam.type, cam.ortho_scale = "ORTHO", 3.5
    co = bpy.data.objects.new("cam", cam)
    sc.collection.objects.link(co)
    co.location, co.rotation_euler = (0, -2, 0), (math.radians(90), 0, 0)
    sc.camera = co
    return sc, lo


def stripes(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = list(img.pixels)
    rows = []
    for y in range(h // 4, 3 * h // 4):
        row = [px[4 * (y * w + x)] for x in range(w // 3, 2 * w // 3)]
        rows.append(sum(row) / len(row))
    bpy.data.images.remove(img)
    mean = sum(rows) / len(rows)
    return (sum((r - mean) ** 2 for r in rows) / len(rows)) ** 0.5 / max(mean, 1e-6)


def main():
    gobocard = install()
    sc, lo = scene()
    engines = [i.identifier for i in sc.render.bl_rna.properties["engine"].enum_items]
    eevee = next(e for e in engines if "EEVEE" in e)
    out = os.environ.get("TLA_OUT", "/tmp")
    failures = []
    for engine in (eevee, "CYCLES"):
        sc.render.engine = engine
        if engine == "CYCLES":
            sc.cycles.samples, sc.cycles.use_denoising = 32, True
        res = {}
        for pid in ("none", "gobo_window_blinds"):
            lo.data.tla_gobo_choice = pid
            gobocard.sync_engine()
            path = os.path.join(out, "gobo_%s_%s.png" % (engine.lower(), pid))
            sc.render.filepath = path
            bpy.ops.render.render(write_still=True)
            res[pid] = stripes(path)
        cards = [o for o in bpy.data.objects if o.get(gobocard.CARD_TAG)]
        shown = [not c.hide_render for c in cards]
        print("%-18s stripe contrast: none %.3f  blinds %.3f  card rendering: %s"
              % (engine, res["none"], res["gobo_window_blinds"], shown))
        if res["gobo_window_blinds"] < 0.25:
            failures.append("%s: the gobo doesn't show (contrast %.3f)" % (engine, res["gobo_window_blinds"]))
        if res["none"] > 0.08:
            failures.append("%s: the plain pool is striped (%.3f)" % (engine, res["none"]))
        if engine == "CYCLES" and any(shown):
            failures.append("CYCLES: the gobo card renders too, so the pattern would be doubled")
    print("OK" if not failures else "FAILED:\n  " + "\n  ".join(failures))
    sys.exit(1 if failures else 0)


main()
