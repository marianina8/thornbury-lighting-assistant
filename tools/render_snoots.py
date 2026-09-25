"""Render the demo scene in Cycles with and without the addon's snoot on each
light type, and check the snoot physically cuts the spill.

    <bpy venv>/bin/python tools/render_snoots.py demo/thornbury_demo.blend [out_dir]
"""
import math
import os
import sys

import bpy
import numpy as np

ROOT = os.environ.get("TLA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "addon"))
from thornbury_lighting import props, snoot  # noqa: E402

props.register()  # the snoot's length/mouth properties
blend = next((a for a in sys.argv[1:] if a.endswith(".blend")), os.path.join(ROOT, "demo", "thornbury_demo.blend"))
out_dir = next((a for a in sys.argv[1:] if not a.endswith((".blend", ".py"))), os.path.join(ROOT, "docs", "img"))
bpy.ops.wm.open_mainfile(filepath=blend)
sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.samples, sc.cycles.use_denoising = 64, True
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = 640, 360, 100

lights = [o for o in bpy.data.objects if o.type == "LIGHT"]
# Compare the addon's snoot against no snoot: set hand-built snoots aside (in
# this in-memory copy only; the .blend on disk is never saved by this script).
handmade = [o for o in bpy.data.objects if o.type == "MESH" and "snoot" in o.name.lower()]
has_handmade = {h.parent.name for h in handmade if h.parent is not None}
for h in handmade:
    bpy.data.objects.remove(h, do_unlink=True)


def render(name):
    path = os.path.join(bpy.app.tempdir or "/tmp", "snoot_%s.exr" % name)
    sc.render.image_settings.file_format = "OPEN_EXR"
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)[::-1, :, :3].mean(axis=2)
    bpy.data.images.remove(img)
    sc.render.image_settings.file_format = "PNG"
    sc.render.filepath = os.path.join(out_dir, "snoot-%s.png" % name)
    bpy.ops.render.render(write_still=True)
    return px


failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


os.makedirs(out_dir, exist_ok=True)
for target_type in ("SPOT", "AREA"):
    # Prefer the light the artist already snooted by hand (the example), then the brightest.
    cands = sorted((o for o in lights if o.data.type == target_type),
                   key=lambda o: (o.name not in has_handmade, -o.data.energy))
    target = cands[0] if cands else None
    if target is None:
        print("no %s light in the scene; skipped" % target_type)
        continue
    for o in lights:
        o.hide_render = o is not target
    tag = target_type.lower()
    before = render(tag + "-without")
    snoot.add(target)
    after = render(tag + "-with")
    lit_b = float((before > 0.05).mean())
    lit_a = float((after > 0.05).mean())
    check(lit_a < 0.8 * lit_b, "%s (%s) snoot cuts the lit area (%.1f%% -> %.1f%% of the frame)" % (tag, target.name, 100 * lit_b, 100 * lit_a))
    s = snoot.find(target)
    size_attr = "shadow_soft_size" if target_type == "SPOT" else "size"
    old = getattr(target.data, size_attr)
    setattr(target.data, size_attr, old * 2)
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev_scale = s.evaluated_get(dg).scale[0]
    setattr(target.data, size_attr, old)
    dg.update()
    check(abs(ev_scale / s.evaluated_get(dg).scale[0] - 2) < 1e-4, "%s snoot doubles when the light's %s doubles" % (tag, size_attr))
    snoot.remove(target)

sys.exit(1 if failures else 0)
