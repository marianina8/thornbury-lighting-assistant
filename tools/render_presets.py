"""Render every preset through the addon's own node wiring in Cycles, check
each one has the intended visible effect, and save a contact sheet.

    BLENDER_USER_RESOURCES=$(mktemp -d) <bpy venv>/bin/python tools/render_presets.py [out.png]

Checks (on a wall lit straight on by the spot):
  * every gobo changes the pool; blinds make horizontal stripes; slot cuts the
    top and bottom; soft iris makes the pool smaller;
  * each IES profile keeps the centre brightness within 10% of the bare light
    (so applying one doesn't change exposure) and has the designed falloff.

Renders are read back as float (EXR) so nothing is judged on clipped pixels.
"""
import math
import os
import sys

import bpy
import numpy as np

ROOT = os.environ.get("TLA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "addon"))
from thornbury_lighting import lightstate  # noqa: E402

W, H = 200, 200


def scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 48
    sc.cycles.use_denoising = False
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.view_settings.view_transform = "Standard"
    sc.render.image_settings.file_format = "OPEN_EXR"
    # A wall at y=0 facing -Y.
    me = bpy.data.meshes.new("wall")
    me.from_pydata([(-4, 0, -4), (4, 0, -4), (4, 0, 4), (-4, 0, 4)], [], [(0, 3, 2, 1)])
    sc.collection.objects.link(bpy.data.objects.new("wall", me))
    cam = bpy.data.cameras.new("cam")
    cam.type, cam.ortho_scale = "ORTHO", 6
    co = bpy.data.objects.new("cam", cam)
    co.location, co.rotation_euler = (0, -6, 0), (math.radians(90), 0, 0)
    sc.collection.objects.link(co)
    sc.camera = co
    light = bpy.data.lights.new("Key", "SPOT")
    light.energy, light.spot_size, light.spot_blend, light.shadow_soft_size = 90, math.radians(60), 0.05, 0.0
    lo = bpy.data.objects.new("Key", light)
    lo.location, lo.rotation_euler = (0, -3, 0), (math.radians(90), 0, 0)  # aimed at the wall
    sc.collection.objects.link(lo)
    return sc, light


def render(sc, name):
    path = os.path.join(bpy.app.tempdir or "/tmp", "tla_%s.exr" % name)
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)[::-1, :, :3].mean(axis=2)  # top row first
    bpy.data.images.remove(img)
    return px


CENTRE = None  # the bare light's centre value, set in main()


def lit_area(px):
    return float((px > 0.3 * CENTRE).mean())


def centre(px):
    c = W // 2
    return float(px[c - 3:c + 4, c - 3:c + 4].mean())


def at_angle(px, deg):
    """Mean value on the wall at `deg` off the beam axis (wall 3 m away, 6 m frame)."""
    c = W // 2
    off = int(round(3 * math.tan(math.radians(deg)) / 6 * W))
    return float(px[c - 2:c + 3, c + off - 1:c + off + 2].mean())


def main():
    out = sys.argv[-1] if sys.argv[-1].endswith(".png") else os.path.join(ROOT, "docs", "img", "preset-library.png")
    sc, light = scene()
    shots = {"none": render(sc, "none")}
    for pid in lightstate.LIBRARY:
        lightstate.set_preset(light, pid)
        shots[pid] = render(sc, pid)
        lightstate.set_preset(light, "none")
    base = shots["none"]
    global CENTRE
    CENTRE = centre(base)
    a0 = lit_area(base)
    failures = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg)
        if not cond:
            failures.append(msg)

    check(a0 > 0.1, "baseline pool is visible (lit area %.2f)" % a0)
    pool = base > 0.3 * CENTRE
    for pid, px in shots.items():
        if pid != "none":
            d = float(np.abs(px - base)[pool].mean() / CENTRE)
            check(d > 0.05, "%s visibly changes the pool (mean change %.0f%% of centre)" % (pid, 100 * d))
    col = shots["gobo_window_blinds"][:, W // 2]
    rows_lit = col > 0.3 * CENTRE
    transitions = int(np.count_nonzero(rows_lit[1:] != rows_lit[:-1]))
    check(transitions >= 8, "blinds make horizontal stripes (%d light/dark transitions down the centre)" % transitions)
    slot = shots["gobo_slot"]
    check(slot[H // 2, W // 2] > 0.3 * CENTRE and slot[int(H * 0.3), W // 2] < 0.25 * slot[H // 2, W // 2],
          "slot keeps the centre band and cuts above it")
    check(lit_area(shots["gobo_soft_iris"]) < 0.7 * a0, "soft iris makes a smaller pool (%.2f vs %.2f)" % (lit_area(shots["gobo_soft_iris"]), a0))
    designed = {
        "ies_narrow_spot": lambda d: math.exp(-0.5 * (d / 8.5) ** 2),
        "ies_medium_soft": lambda d: math.exp(-0.5 * (d / 19.0) ** 2),
        "ies_wide_flood": lambda d: max(0.0, math.cos(math.radians(d))) ** 3,
    }
    for pid, curve in designed.items():
        px = shots[pid]
        gain = centre(px) / CENTRE
        check(0.9 <= gain <= 1.1, "%s keeps centre brightness (gain %.3f)" % (pid, gain))
        worst = max(abs(at_angle(px, d) / at_angle(base, d) / gain - curve(d)) for d in (5, 10, 15, 20, 25))
        check(worst < 0.05, "%s falloff matches its design within 0.05 (worst %.3f)" % (pid, worst))
    n, w = lit_area(shots["ies_narrow_spot"]), lit_area(shots["ies_wide_flood"])
    check(n < 0.6 * a0, "narrow IES makes a smaller pool (%.2f vs %.2f)" % (n, a0))
    check(w > n * 1.5, "wide flood is wider than narrow spot (%.2f vs %.2f)" % (w, n))

    # The picker's transform really moves the projected pattern.
    def transitions(px, axis):
        line = px[:, W // 2] if axis == "col" else px[H // 2, :]
        lit = line > 0.3 * CENTRE
        return int(np.count_nonzero(lit[1:] != lit[:-1]))

    lightstate.set_preset(light, "gobo_blinds_wide")
    flat = render(sc, "blinds0")
    xf = light.node_tree.nodes["TLA Gobo Transform"]
    xf.inputs["Rotation"].default_value = (0, 0, -math.radians(90))
    turned = render(sc, "blinds90")
    xf.inputs["Rotation"].default_value = (0, 0, 0)
    xf.inputs["Scale"].default_value = (0.5, 0.5, 1)  # size 2
    bigger = render(sc, "blinds_big")
    lightstate.set_preset(light, "none")
    check(transitions(flat, "col") >= 6 and transitions(flat, "row") <= 2,
          "horizontal blinds: stripes run across (%d down, %d across)" % (transitions(flat, "col"), transitions(flat, "row")))
    check(transitions(turned, "row") >= 6 and transitions(turned, "col") <= 2,
          "rotate 90° turns them vertical (%d across, %d down)" % (transitions(turned, "row"), transitions(turned, "col")))
    check(transitions(bigger, "col") < transitions(flat, "col"),
          "size 2 makes the slats bigger (%d -> %d stripes)" % (transitions(flat, "col"), transitions(bigger, "col")))

    # Contact sheet: 3 x 3 grid with labels drawn by PIL if available.
    try:
        from PIL import Image, ImageDraw
        tiles = list(shots.items())
        cols = 7
        rows = (len(tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * W, rows * (H + 22)), (18, 18, 18))
        d = ImageDraw.Draw(sheet)
        for i, (pid, px) in enumerate(tiles):
            x, y = (i % cols) * W, (i // cols) * (H + 22)
            tile = Image.fromarray((np.clip(px / (1.1 * CENTRE), 0, 1) ** (1 / 2.2) * 255).astype(np.uint8))
            sheet.paste(tile.convert("RGB"), (x, y))
            label = "No preset" if pid == "none" else lightstate.LIBRARY[pid]["label"]
            d.text((x + 6, y + H + 5), label, fill=(230, 230, 230))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        sheet.save(out)
        print("wrote", out)
    except ImportError:
        print("Pillow not available; skipped the contact sheet")
    sys.exit(1 if failures else 0)


main()
