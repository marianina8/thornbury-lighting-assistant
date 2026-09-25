#!/usr/bin/env python3
"""Generate the bundled gobo/IES preset library deterministically.

Every asset here is synthetic and made by this script (no third-party gobo
artwork or manufacturer photometry), so the library is free to redistribute.

Writes:
  internal/presets/presets.json          (source of truth, embedded by Go)
  addon/thornbury_lighting/presets/*     (images, .ies files, presets.json copy)

Usage: python3 tools/gen_presets.py   (needs numpy + Pillow; only run by developers)
"""
import json
import math
import os
import shutil

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, "addon", "thornbury_lighting", "presets")
GO_JSON = os.path.join(ROOT, "internal", "presets", "presets.json")
N = 512

yy, xx = np.mgrid[0:N, 0:N]
u = (xx + 0.5) / N  # 0..1 left->right
v = 1.0 - (yy + 0.5) / N  # 0..1 bottom->top (Blender image convention)


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def save(name, a):
    a = np.clip(a, 0, 1)
    Image.fromarray((a * 255).round().astype(np.uint8), "L").save(os.path.join(ADDON_DIR, name), optimize=True)


def value_noise(seed, cells):
    rng = np.random.default_rng(seed)
    g = rng.random((cells + 1, cells + 1))
    x, y = u * cells, v * cells
    i, j = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = smoothstep(0, 1, x - i), smoothstep(0, 1, y - j)
    a, b = g[j, i], g[j, i + 1]
    c, d = g[j + 1, i], g[j + 1, i + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def rot(angle_deg):
    """u, v rotated about the centre."""
    a = math.radians(angle_deg)
    x, y = u - 0.5, v - 0.5
    return x * math.cos(a) - y * math.sin(a) + 0.5, x * math.sin(a) + y * math.cos(a) + 0.5


def slats(t, count, open_frac, soft=0.03):
    """Parallel slats across t: `open_frac` of each period lets light through."""
    ph = (t * count) % 1.0
    lo = 1.0 - open_frac
    return smoothstep(lo - soft, lo + soft, ph) * (1 - smoothstep(1 - soft, 1.0, ph))


def frame_mask(margin, soft=0.015):
    return (smoothstep(margin, margin + soft, u) * smoothstep(margin, margin + soft, 1 - u)
            * smoothstep(margin, margin + soft, v) * smoothstep(margin, margin + soft, 1 - v))


def mullions(t, centres, half_width, soft=0.012):
    m = np.ones_like(t)
    for c in centres:
        m = m * smoothstep(half_width, half_width + soft, np.abs(t - c))
    return m


def voronoi_f1(seed, count):
    rng = np.random.default_rng(seed)
    pts = rng.random((count, 2))
    d = np.full(u.shape, 9.0)
    for px, py in pts:
        for ox in (-1, 0, 1):  # tile so edges wrap cleanly
            for oy in (-1, 0, 1):
                d = np.minimum(d, np.hypot(u - px - ox, v - py - oy))
    return d


def voronoi_edges(seed, count):
    rng = np.random.default_rng(seed)
    pts = rng.random((count, 2))
    d1 = np.full(u.shape, 9.0)
    d2 = np.full(u.shape, 9.0)
    for px, py in pts:
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                d = np.hypot(u - px - ox, v - py - oy)
                d2 = np.minimum(d2, np.maximum(d1, d))
                d1 = np.minimum(d1, d)
    return d2 - d1


def strokes(seed, count, length, width):
    """Branch-like tapered strokes (random walks) that block light."""
    rng = np.random.default_rng(seed)
    block = np.zeros(u.shape)
    for _ in range(count):
        x, y = rng.random(2)
        ang = rng.uniform(0, 2 * math.pi)
        w = width * rng.uniform(0.6, 1.4)
        for step in range(int(length)):
            ang += rng.normal(0, 0.25)
            x += math.cos(ang) * 0.012
            y += math.sin(ang) * 0.012
            ww = w * (1 - step / length) + 0.002
            block = np.maximum(block, 1 - smoothstep(ww * 0.6, ww, np.hypot(u - x, v - y)))
            if rng.random() < 0.04:  # twigs
                bx, by, ba = x, y, ang + rng.choice((-1, 1)) * rng.uniform(0.5, 1.1)
                for k in range(12):
                    bx += math.cos(ba) * 0.01
                    by += math.sin(ba) * 0.01
                    block = np.maximum(block, 1 - smoothstep(ww * 0.3, ww * 0.5, np.hypot(u - bx, v - by)))
    return 1 - block


def gobos():
    """Every gobo, keyed by file name. The original five are unchanged."""
    r = np.hypot(u - 0.5, v - 0.5)
    ang = np.arctan2(v - 0.5, u - 0.5)
    out = {}
    # --- windows and blinds
    phase = (v * 9) % 1.0
    out["gobo_window_blinds"] = smoothstep(0.40, 0.46, phase) * (1 - smoothstep(0.94, 1.0, phase))
    out["gobo_blinds_wide"] = slats(v, 5, 0.5, 0.02)
    ru, rv = rot(30)
    out["gobo_blinds_angled"] = slats(rv, 9, 0.55, 0.03)
    out["gobo_blinds_vertical"] = slats(u, 11, 0.55, 0.03)
    bar = lambda t, c, w: smoothstep(w, w + 0.012, np.abs(t - c))
    out["gobo_window_panes"] = bar(u, 0.5, 0.018) * bar(v, 0.5, 0.018) * (
        smoothstep(0.08, 0.095, u) * smoothstep(0.08, 0.095, 1 - u) * smoothstep(0.08, 0.095, v) * smoothstep(0.08, 0.095, 1 - v))
    out["gobo_window_grid"] = mullions(u, (1 / 3, 2 / 3), 0.012) * mullions(v, (1 / 3, 2 / 3), 0.012) * frame_mask(0.06)
    half_w, spring, top = 0.28, 0.58, 0.06
    rect = (smoothstep(-0.008, 0.008, half_w - np.abs(u - 0.5)) * smoothstep(-0.008, 0.008, v - top)
            * (1 - smoothstep(-0.004, 0.004, v - spring)))
    dome = (1 - smoothstep(half_w - 0.008, half_w + 0.008, np.hypot(u - 0.5, v - spring))) * smoothstep(-0.004, 0.004, v - spring)
    out["gobo_window_arched"] = np.maximum(rect, dome) * mullions(u, (0.5,), 0.012) * mullions(v, (0.32, spring), 0.012)
    du, dv = rot(45)
    out["gobo_window_diamond"] = mullions((du * 7) % 1, (0.0, 1.0), 0.035, 0.02) * mullions((dv * 7) % 1, (0.0, 1.0), 0.035, 0.02) * frame_mask(0.06)
    # --- foliage and breakup
    n = 0.65 * value_noise(7, 9) + 0.35 * value_noise(11, 23)
    out["gobo_leaf_breakup"] = smoothstep(0.50, 0.58, n)
    n2 = 0.55 * value_noise(21, 14) + 0.45 * value_noise(22, 37)
    out["gobo_leaf_dense"] = smoothstep(0.60, 0.66, n2)
    n3 = 0.7 * value_noise(31, 6) + 0.3 * value_noise(32, 17)
    out["gobo_leaf_sparse"] = smoothstep(0.36, 0.46, n3)
    out["gobo_branches"] = strokes(41, 9, 70, 0.022)
    n4 = 0.6 * value_noise(51, 5) + 0.4 * value_noise(52, 11)
    out["gobo_breakup_soft"] = 0.35 + 0.65 * smoothstep(0.3, 0.7, n4)
    e = voronoi_edges(61, 30)
    out["gobo_water_caustic"] = 0.12 + 0.88 * (1 - smoothstep(0.004, 0.018, e))
    # --- shapes and cuts
    out["gobo_soft_iris"] = 1 - smoothstep(0.22, 0.34, r)
    out["gobo_hard_iris"] = 1 - smoothstep(0.195, 0.205, r)
    out["gobo_ring"] = smoothstep(0.22, 0.25, r) * (1 - smoothstep(0.33, 0.36, r))
    out["gobo_slot"] = 1 - smoothstep(0.10, 0.16, np.abs(v - 0.5))
    out["gobo_slot_vertical"] = 1 - smoothstep(0.07, 0.11, np.abs(u - 0.5))
    out["gobo_barn_doors_corner"] = smoothstep(-0.02, 0.02, (u - 0.25)) * smoothstep(-0.02, 0.02, (0.8 - v)) * smoothstep(-0.03, 0.03, (1.25 - u - v))
    # --- graphic
    gu, gv = (u * 8) % 1 - 0.5, (v * 8) % 1 - 0.5
    out["gobo_dots"] = 1 - smoothstep(0.17, 0.2, np.hypot(gu, gv))
    rng = np.random.default_rng(71)
    stars = np.zeros(u.shape)
    for _ in range(70):
        sx, sy, sr = rng.random(), rng.random(), rng.uniform(0.008, 0.02)
        stars = np.maximum(stars, 1 - smoothstep(sr * 0.5, sr, np.hypot(u - sx, v - sy)))
    out["gobo_stars"] = stars
    out["gobo_bars"] = slats(u, 7, 0.78, 0.01)
    fu, fv = rot(45)
    out["gobo_fence"] = mullions((fu * 17) % 1, (0.0, 1.0), 0.045, 0.02) * mullions((fv * 17) % 1, (0.0, 1.0), 0.045, 0.02)
    for name, a in out.items():
        save(name + ".png", a)
    return out


# Peak candela chosen so a preset keeps the light's centre brightness: Cycles
# scales IES output by 0.0770 per candela in Blender 4.2-4.5 and 0.0707 in
# 5.0-5.2 (measured with float renders, tools/render_presets.py). 13.54 cd is
# the geometric mean, giving a centre gain of 1.04 (4.x) / 0.96 (5.x), about
# +/-0.06 stops. A 1000 cd peak made presets ~70x (6 stops) too bright.
IES_PEAK_CD = 13.54


def write_ies(name, label, falloff):
    """LM-63-2002, type C photometry, rotationally symmetric.
    Vertical angle 0 is straight along the beam (nadir)."""
    angles = list(range(0, 181, 5))
    cd = [round(IES_PEAK_CD * falloff(math.radians(a)), 4) for a in angles]
    lines = [
        "IESNA:LM-63-2002",
        "[TEST] synthetic profile generated by tools/gen_presets.py",
        "[MANUFAC] Thornbury VFX demo library (synthetic, not a real fixture)",
        f"[LUMCAT] {name}",
        f"[LUMINAIRE] {label}",
        "TILT=NONE",
        f"1 -1 1 {len(angles)} 1 1 2 0 0 0",
        "1 1 0",
        " ".join(str(a) for a in angles),
        "0",
        " ".join(str(c) for c in cd),
    ]
    with open(os.path.join(ADDON_DIR, name + ".ies"), "w", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def ies():
    gauss = lambda half: (lambda t: math.exp(-0.5 * (t / half) ** 2))
    write_ies("ies_narrow_spot", "Narrow spot (~20 degree beam)", gauss(math.radians(8.5)))
    write_ies("ies_medium_soft", "Medium soft beam (~45 degree)", gauss(math.radians(19)))
    write_ies("ies_wide_flood", "Wide flood (~90 degree field, soft shoulder)", lambda t: max(0.0, math.cos(t)) ** 3)


def G(pid, label, family, use_when, ext="EXTEND"):
    return {"id": pid, "kind": "gobo", "label": label, "family": family, "file": pid + ".png",
            "extension": ext, "use_when": use_when}


PRESETS = [
    # Windows and blinds
    G("gobo_window_blinds", "Window blinds", "Windows and blinds",
      "breaks the beam into horizontal slat shadows, like light through venetian blinds", "REPEAT"),
    G("gobo_blinds_wide", "Wide blinds", "Windows and blinds", "a few thick horizontal slats; bold, graphic blind shadows", "REPEAT"),
    G("gobo_blinds_angled", "Angled blinds", "Windows and blinds", "diagonal slat shadows, like late sun raking through blinds", "REPEAT"),
    G("gobo_blinds_vertical", "Vertical blinds", "Windows and blinds", "vertical slat shadows, like vertical blinds or louvres", "REPEAT"),
    G("gobo_window_panes", "Window panes", "Windows and blinds", "projects a four-pane window frame, like sunlight through a window"),
    G("gobo_window_grid", "Window grid", "Windows and blinds", "a nine-pane window (3 by 3 mullions)"),
    G("gobo_window_arched", "Arched window", "Windows and blinds", "a tall arched window with mullions; churches, old houses"),
    G("gobo_window_diamond", "Leaded diamonds", "Windows and blinds", "a leaded diamond-lattice window; period or storybook interiors"),
    # Foliage and breakup
    G("gobo_leaf_breakup", "Leaf breakup", "Foliage and breakup",
      "dappled, broken-up light, like light through foliage; breaks up a flat wash", "REPEAT"),
    G("gobo_leaf_dense", "Dense canopy", "Foliage and breakup", "mostly shadow with small bright dapples, like a thick tree canopy", "REPEAT"),
    G("gobo_leaf_sparse", "Sparse leaves", "Foliage and breakup", "mostly light with a few leaf shadows; gentle outdoor breakup", "REPEAT"),
    G("gobo_branches", "Bare branches", "Foliage and breakup", "thin branch and twig shadows, like winter trees"),
    G("gobo_breakup_soft", "Soft breakup", "Foliage and breakup", "low-contrast cloudy breakup that just takes the flatness off a wash", "REPEAT"),
    G("gobo_water_caustic", "Water caustics", "Foliage and breakup", "a bright web of caustic lines, like light reflected off a pool", "REPEAT"),
    # Shapes and cuts
    G("gobo_soft_iris", "Soft iris", "Shapes and cuts", "a soft round pool tighter than the cone; a snoot-like cut with a feathered edge"),
    G("gobo_hard_iris", "Hard iris", "Shapes and cuts", "a small, hard-edged round pool; a pin spot"),
    G("gobo_ring", "Ring", "Shapes and cuts", "a ring of light with a dark centre; a halo"),
    G("gobo_slot", "Slot (barn doors)", "Shapes and cuts", "cuts the beam top and bottom into a horizontal band, like closing barn doors"),
    G("gobo_slot_vertical", "Door slit", "Shapes and cuts", "a narrow vertical band, like light through a door left ajar"),
    G("gobo_barn_doors_corner", "Corner cut", "Shapes and cuts", "cuts the beam off two sides and a corner, like angled barn doors"),
    # Graphic
    G("gobo_dots", "Dot grid", "Graphic", "a regular grid of dots; graphic, stagey", "REPEAT"),
    G("gobo_stars", "Stars", "Graphic", "scattered pinpoints of light, like a star cloth"),
    G("gobo_bars", "Bars", "Graphic", "thin vertical bars, like a cell or railings", "REPEAT"),
    G("gobo_fence", "Chain-link fence", "Graphic", "a diamond chain-link fence shadow", "REPEAT"),
    # Beam profiles (IES)
    {"id": "ies_narrow_spot", "kind": "ies", "label": "Narrow spot profile", "family": "Beam profiles", "file": "ies_narrow_spot.ies",
     "use_when": "a hot centre with fast falloff inside the cone (about 20 degree beam)"},
    {"id": "ies_medium_soft", "kind": "ies", "label": "Medium soft profile", "family": "Beam profiles", "file": "ies_medium_soft.ies",
     "use_when": "an even beam with a gentle shoulder (about 45 degrees)"},
    {"id": "ies_wide_flood", "kind": "ies", "label": "Wide flood profile", "family": "Beam profiles", "file": "ies_wide_flood.ies",
     "use_when": "broad flood whose brightness rolls off gently toward the edge of a wide cone (about 90 degree field)"},
]


def main():
    os.makedirs(ADDON_DIR, exist_ok=True)
    gobos()
    ies()
    doc = {"version": 1, "presets": PRESETS}
    os.makedirs(os.path.dirname(GO_JSON), exist_ok=True)
    with open(GO_JSON, "w", newline="\n") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    shutil.copyfile(GO_JSON, os.path.join(ADDON_DIR, "presets.json"))
    print("wrote", len(PRESETS), "presets to", ADDON_DIR)


if __name__ == "__main__":
    main()
