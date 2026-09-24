# Blender API verification (2026-09-24)

Everything the addon reads or writes was checked against **real Blender
builds** (the official `bpy` wheels from PyPI) rather than cached knowledge or
docs pages: **4.2.0, 4.5.14 LTS, 5.0.1, 5.1.2 and 5.2.2 LTS** (5.2 is the
current release). The addon's minimum is 4.2, the first version with the
extension format that "Install from Disk" uses for a `blender_manifest.toml`
package.

## SpotLight properties (RNA, identical in all five versions)

| Property | Type | Hard range | Soft range | Default | Notes |
|---|---|---|---|---|---|
| `spot_size` | float, ANGLE | 0.0174533 – 3.1415927 rad (1°–180°) | same | 0.785398 (45°) | Blender clamps on assignment |
| `spot_blend` | float | 0 – 1 | 0 – 1 | 0.15 | |
| `energy` | float (W) | **−FLT_MAX – FLT_MAX** | 0 – 1,000,000 | 10 | **Negative values are accepted by Blender.** "≥ 0" is our policy, enforced in Go and again in the addon |
| `color` | float[3], COLOR | 0 – FLT_MAX | 0 – 1 | (1,1,1) | we propose 0–1 only |
| `shadow_soft_size` | float, DISTANCE (m) | 0 – FLT_MAX | 0 – 100 | 0 (RNA default) | we cap at 100 m |
| `use_square` | bool | | | False | |
| `use_temperature`, `temperature` | bool, float (K) | 800 – 20000 | same | False, 6500 | **4.5+ only.** Missing in 4.2; the backend converts a proposed temperature to RGB for 4.2–4.4 |
| `use_nodes` | bool | | | **False up to 5.0, True from 5.1** | **5.2 warns it is "expected to be removed in Blender 6.0"**; the addon only writes it when it must change, with the warning suppressed |

`energy`'s RNA subtype changed from POWER (4.x) to NONE (5.x); the value is
still watts. None of this affects the addon.

## Light node tree

With nodes on, a new light's tree is `Emission (Color, Strength, Weight) ->
Light Output (Surface)` in every version. Light node trees only render in
**Cycles** (EEVEE ignores them); the panel says so when a preset is proposed
in an EEVEE scene.

### Gobo (Image Texture), confirmed by Cycles test renders

`Texture Coordinate.Normal` on a light is the outgoing ray direction **in the
light's local space**. Verified by rendering a quadrant test image (it lands
in the matching quadrant) and then rotating the light 90° about Z (the
pattern rotates with it). The addon projects it:

```
uv = Normal.xy / -Normal.z * k + 0.5,   k = 0.5 / tan(spot_size / 2)
Image.Color -> Emission.Color
```

so the image exactly fills the cone. When a later suggestion changes
`spot_size`, the addon updates `k`. Images are packed into the .blend and set
to Non-Color.

### IES Texture

`IES.output[0] -> Emission.Strength`. **The output socket was renamed `Fac`
(4.2, 4.5) → `Factor` (5.0+)**, so the addon links by index, never by name.
Profiles are stored as internal text blocks (`mode = 'INTERNAL'`) so the .blend
is portable. With the IES Vector input unconnected the lookup is the same as
wiring `Texture Coordinate.Normal` (identical renders), and vertical angle 0°
is the beam axis (flipping the vector turns the light black).

**Gain (found by the render checks):** Cycles scales IES output by
**0.0770 per candela (4.2, 4.5)** and **0.0707 per candela (5.0 – 5.2)**,
measured on float (EXR) renders; the gain is exactly linear in candela. A
first version of the library used a 1000 cd peak, which made every IES preset
~70× (six stops) brighter than the bare light. It was invisible in 8-bit PNG
checks because the images clipped. The profiles now peak at **13.54 cd** (the
geometric mean), giving a centre gain of 1.03 on 4.x and 0.95 on 5.x
(±0.06 stops), and each profile's falloff matches its design curve within
0.021 on every version.

## How to re-run

```
make addon-test      # installs dist/*.zip like "Install from Disk", 26 tests per version (undo skipped; 3 skipped without a backend)
make render-check    # Cycles renders of every preset, 20 checks per version
```

See README > Testing the addon for setting up the `bpy` wheels. The one thing
headless Blender can't do is **undo**: `bpy.ops.ed.undo` needs a window. The
Apply operator carries `bl_options = {'REGISTER', 'UNDO'}` (asserted by a
test), but a real Ctrl+Z has to be checked once by hand in the Blender UI (see
the tester guide).
