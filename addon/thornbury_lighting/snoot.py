# SPDX-License-Identifier: GPL-3.0-or-later
"""Physical snoots: an open, tapered tube (round) or box parented to a spot or
area light, which physically blocks light the way a real snoot does.

Proportions come from the two hand-built examples in demo/thornbury_demo.blend
(measured in each light's local space on 2026-09-24):

  spot snoot:  round, 32 sides; back radius = 1.08 x light radius
               (shadow_soft_size); mouth = 0.5 x back; length = 1.0 x back width
  area snoot:  box; back width = 1.008 x light size; mouth = 0.5 x back;
               length = 2.6 x back width (disk/ellipse area lights get a round tube)

The snoot "scales with the light": its object scale is driven by the light's
own size (radius for spot, size/size_y for area) through simple-expression
drivers, which Blender evaluates without needing "auto-run Python scripts".
Because it's parented to the light, it also follows the light's transform
and object scale. Length and mouth are stored on the snoot and rebuild the
mesh when changed.
"""

import math
import re

import bmesh
import bpy

SNOOT_SUFFIX = ".Snoot"
MATERIAL_NAME = "TLA Snoot Black"
ROUND_SEGMENTS = 32

# Back half-width per unit of light size. Rectangle and ellipse area lights use
# size_y for the snoot's y axis; square and disk use size for both. The shape
# is baked into the drivers at refit time (and refit when the shape changes)
# rather than read in the expression: AreaLight.shape's enum values are
# SQUARE=0, RECTANGLE=1, DISK=4, ELLIPSE=5, not 0-3.
SPOT_BACK = 1.08       # x shadow_soft_size (radius)
AREA_BACK = 0.504      # x size (half of a width that's 1.008 x size)
SPOT_MIN_RADIUS = 0.05  # a 0 m radius light still gets a 5.4 cm snoot
AREA_MIN_SIZE = 0.02

DEFAULTS = {"SPOT": {"length": 1.0, "mouth": 0.5}, "AREA": {"length": 2.6, "mouth": 0.5}}
LENGTH_RANGE = (0.25, 6.0)  # x back width
MOUTH_RANGE = (0.2, 1.0)    # x back width (1.0 = straight tube)


def supported(light):
    return light is not None and light.type in {"SPOT", "AREA"}


def is_round(light):
    return light.type == "SPOT" or light.shape in {"DISK", "ELLIPSE"}


# ------------------------------------------------------------------ finding

def find(light_obj):
    """The addon-managed snoot object for a light object, or None."""
    for ch in light_obj.children:
        if ch.type == "MESH" and ch.get("tla_snoot"):
            return ch
    return None


_HANDMADE_NAME = re.compile(r"(^|[\s._-])snoot(\.\d{3})?$", re.IGNORECASE)


def _looks_like_snoot(light_obj, obj):
    """An open tube or box along the light's forward axis, at least as wide at
    the back as at the mouth."""
    try:
        m = measure(light_obj, obj)
    except (ValueError, ZeroDivisionError):
        return False
    return 0.05 <= m["mouth"] <= 1.05 and 0.1 <= m["length"] <= 20


def handmade_candidates(light_obj):
    """Hand-built snoots: mesh children named like "Spot.Snoot" / "Key Snoot"
    that have a snoot's shape. Anything else (a "Snoot bracket") is ignored."""
    return [ch for ch in light_obj.children
            if ch.type == "MESH" and not ch.get("tla_snoot") and not ch.get("tla_replaced")
            and _HANDMADE_NAME.search(ch.name) and _looks_like_snoot(light_obj, ch)]


def find_handmade(light_obj):
    c = handmade_candidates(light_obj)
    return c[0] if c else None


def replaced_original(light_obj):
    """A hand-built snoot hidden by Convert, which Restore can bring back."""
    for ch in light_obj.children:
        if ch.get("tla_replaced"):
            return ch
    return None


def state(light_obj):
    """What the assistant sees: present / length / mouth, or 'custom'."""
    s = find(light_obj)
    if s is not None:
        return {"snoot": True, "snoot_length": float(s.tla_snoot_length), "snoot_mouth": float(s.tla_snoot_mouth)}
    if find_handmade(light_obj) is not None:
        return {"snoot": True, "snoot_custom": True}
    return {"snoot": False}


# ----------------------------------------------------------------- geometry

# A spot light emits from a sphere centred on the light, so light leaving its
# back half at an angle would escape around a tube that starts at the centre
# (found in a Cycles render of the showcase scene). A straight collar behind
# the centre covers the source, like a snoot bolted onto a fixture housing;
# the visible front shape is unchanged. Area lights emit forward from a flat
# panel at z=0 and need no collar.
SPOT_COLLAR = 1.0  # in back half-widths (so it covers the whole sphere)


def _build_mesh(me, rnd, length, mouth, collar=0.0):
    """Unit snoot in light-local space: back half-width 1 at z=0, mouth
    half-width `mouth` at z = -2*length (length is in back widths), plus an
    optional straight collar from z=0 back to z=+collar."""
    bm = bmesh.new()
    z1 = -2.0 * length
    if rnd:
        n = ROUND_SEGMENTS
        ring = lambda r, z: [bm.verts.new((r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z)) for i in range(n)]
    else:
        corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        ring = lambda r, z: [bm.verts.new((r * x, r * y, z)) for x, y in corners]
    rings = ([ring(1.0, collar)] if collar > 0 else []) + [ring(1.0, 0.0), ring(mouth, z1)]
    for a, b in zip(rings, rings[1:]):
        n = len(a)
        for i in range(n):
            j = (i + 1) % n
            bm.faces.new((a[i], a[j], b[j], b[i]))  # open at both ends, like a real snoot
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    if rnd:
        for p in me.polygons:
            p.use_smooth = True
    me.update()


def _material():
    m = bpy.data.materials.get(MATERIAL_NAME)
    if m is None:
        m = bpy.data.materials.new(MATERIAL_NAME)
        m.diffuse_color = (0.02, 0.02, 0.02, 1.0)  # viewport colour
        m.use_nodes = True
        bsdf = next((n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (0.02, 0.02, 0.02, 1.0)  # black wrap: little bounce
            bsdf.inputs["Roughness"].default_value = 0.9
    return m


def _var(driver, name, light, path):
    v = driver.variables.new()
    v.name, v.type = name, "SINGLE_PROP"
    v.targets[0].id_type = "LIGHT"
    v.targets[0].id = light
    v.targets[0].data_path = path


def _drive(obj, light):
    """Object scale = the light's size, so the snoot scales with the light."""
    for i in range(3):
        try:
            obj.driver_remove("scale", i)
        except TypeError:
            pass
    if light.type == "SPOT":
        exprs = ["max(r, %g) * %g" % (SPOT_MIN_RADIUS, SPOT_BACK)] * 3
        names = {"r": "shadow_soft_size"}
    elif light.shape in {"RECTANGLE", "ELLIPSE"}:
        exprs = ["max(sx, %g) * %g" % (AREA_MIN_SIZE, AREA_BACK),
                 "max(sy, %g) * %g" % (AREA_MIN_SIZE, AREA_BACK),
                 "max(sx, sy, %g) * %g" % (AREA_MIN_SIZE, AREA_BACK)]  # length follows the larger side
        names = {"sx": "size", "sy": "size_y"}
    else:  # SQUARE, DISK
        exprs = ["max(sx, %g) * %g" % (AREA_MIN_SIZE, AREA_BACK)] * 3
        names = {"sx": "size"}
    for i, expr in enumerate(exprs):
        d = obj.driver_add("scale", i).driver
        d.type = "SCRIPTED"
        for name, path in names.items():
            if name in expr:
                _var(d, name, light, path)
        d.expression = expr


def _clamp(v, lo, hi):
    return max(lo, min(hi, float(v)))


def add(light_obj, length=None, mouth=None, _replacing=None):
    """Create (or refit) the managed snoot for a spot or area light object."""
    light = light_obj.data
    if not supported(light):
        raise ValueError("Snoots work on spot and area lights.")
    others = [c for c in handmade_candidates(light_obj) if c is not _replacing]
    if others and find(light_obj) is None:
        raise ValueError("This light already has a hand-built snoot (%s). Convert it or remove it first."
                         % others[0].name)
    d = DEFAULTS[light.type]
    obj = find(light_obj)
    if obj is None:
        me = bpy.data.meshes.new(light_obj.name + SNOOT_SUFFIX)
        obj = bpy.data.objects.new(light_obj.name + SNOOT_SUFFIX, me)
        for coll in light_obj.users_collection:
            coll.objects.link(obj)
        obj.parent = light_obj
        obj.matrix_parent_inverse.identity()  # live in the light's own space
        obj.location, obj.rotation_euler = (0, 0, 0), (0, 0, 0)
        obj["tla_snoot"] = True
        obj.data.materials.append(_material())
        _set(obj, d["length"], d["mouth"])
    _set(obj, None if length is None else _clamp(length, *LENGTH_RANGE),
         None if mouth is None else _clamp(mouth, *MOUTH_RANGE))
    refit(light_obj)
    return obj


_BUSY = set()


def _set(obj, length, mouth):
    """Write the registered properties (Blender 5 stores bpy.props apart from
    obj["..."] custom properties) without triggering a rebuild per field."""
    _BUSY.add(obj.name)
    try:
        if length is not None:
            obj.tla_snoot_length = length
        if mouth is not None:
            obj.tla_snoot_mouth = mouth
    finally:
        _BUSY.discard(obj.name)


def refit(light_obj):
    """Rebuild the mesh and drivers (after a length/mouth change, or when an
    area light switches between square and round shapes)."""
    obj = find(light_obj)
    if obj is None:
        return None
    light = light_obj.data
    obj["tla_round"] = is_round(light)
    obj["tla_shape"] = light.shape if light.type == "AREA" else "SPOT"
    if bpy.app.version >= (5, 0, 0):
        # Files from 4.x kept these as custom properties; in 5.x bpy.props live
        # apart, so drop the stale duplicates that would show in the UI.
        for k in ("tla_snoot_length", "tla_snoot_mouth"):
            if k in obj.keys():
                del obj[k]
    _build_mesh(obj.data, obj["tla_round"], float(obj.tla_snoot_length), float(obj.tla_snoot_mouth),
                collar=SPOT_COLLAR if light.type == "SPOT" else 0.0)
    _drive(obj, light)
    return obj


def needs_refit(light_obj):
    obj = find(light_obj)
    if obj is None:
        return False
    light = light_obj.data
    return obj.get("tla_shape") != (light.shape if light.type == "AREA" else "SPOT")


def remove(light_obj):
    obj = find(light_obj)
    if obj is None:
        return False
    me = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if me.users == 0:
        bpy.data.meshes.remove(me)
    return True


# ------------------------------------------------ converting a hand-built one

def measure(light_obj, snoot_obj):
    """Length and mouth ratios of any open, tapered tube parented to a light,
    measured in the light's local space (used to convert hand-built snoots)."""
    M = light_obj.matrix_world.inverted() @ snoot_obj.matrix_world
    vs = [M @ v.co for v in snoot_obj.data.vertices]
    if len(vs) < 6:
        raise ValueError("%s doesn't look like a snoot (too few vertices)." % snoot_obj.name)
    zs = [v.z for v in vs]
    zmax, zmin = max(zs), min(zs)
    eps = (zmax - zmin) * 0.02

    def half(z0):
        ring = [v for v in vs if abs(v.z - z0) <= eps]
        return max(max(abs(v.x), abs(v.y)) for v in ring)

    back, front = half(zmax), half(zmin)
    if back <= 0 or zmax - zmin <= 0:
        raise ValueError("%s has no depth along the light's direction." % snoot_obj.name)
    return {"length": (zmax - zmin) / (2 * back), "mouth": front / back}


def convert_handmade(light_obj):
    """Replace a hand-built snoot with a managed one of the same proportions,
    which then scales with the light. Nothing is touched unless the managed
    snoot is built successfully; the original is hidden, not deleted, and
    Restore brings it back."""
    cands = handmade_candidates(light_obj)
    if not cands:
        raise ValueError("No hand-built snoot found on this light.")
    if len(cands) > 1:
        raise ValueError("More than one hand-built snoot (%s); remove or rename the extras first."
                         % ", ".join(c.name for c in cands))
    src = cands[0]
    m = measure(light_obj, src)
    obj = add(light_obj, length=m["length"], mouth=m["mouth"], _replacing=src)
    src["tla_original_name"] = src.name
    src["tla_replaced"] = True
    src.name = src.name + " (original)"
    src.hide_viewport = src.hide_render = True
    src.hide_set(True)
    obj.name = obj.data.name = light_obj.name + SNOOT_SUFFIX  # the managed one takes the clean name
    return obj, m


def restore_original(light_obj):
    """Bring back a hand-built snoot hidden by Convert (removing the managed one)."""
    src = replaced_original(light_obj)
    if src is None:
        raise ValueError("No hidden hand-built snoot on this light.")
    remove(light_obj)
    src.name = src.get("tla_original_name", src.name)
    for k in ("tla_replaced", "tla_original_name"):
        if k in src:
            del src[k]
    src.hide_viewport = src.hide_render = False
    src.hide_set(False)
    return src
