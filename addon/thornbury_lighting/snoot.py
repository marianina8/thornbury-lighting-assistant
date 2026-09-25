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

import bmesh
import bpy

SNOOT_SUFFIX = ".Snoot"
MATERIAL_NAME = "TLA Snoot Black"
ROUND_SEGMENTS = 32

# Back half-width per unit of light size, and the driver expressions that
# implement it. "shape == 1 or shape == 3" means RECTANGLE or ELLIPSE (size_y used).
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


def find_handmade(light_obj):
    """A hand-built snoot (a mesh child named '*.Snoot' that isn't ours)."""
    for ch in light_obj.children:
        if ch.type == "MESH" and not ch.get("tla_snoot") and not ch.get("tla_replaced") \
                and "snoot" in ch.name.lower():
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

def _build_mesh(me, rnd, length, mouth):
    """Unit snoot in light-local space: back half-width 1 at z=0, mouth
    half-width `mouth` at z = -2*length (length is in back widths)."""
    bm = bmesh.new()
    z1 = -2.0 * length
    if rnd:
        n = ROUND_SEGMENTS
        back = [bm.verts.new((math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n), 0.0)) for i in range(n)]
        front = [bm.verts.new((mouth * math.cos(2 * math.pi * i / n), mouth * math.sin(2 * math.pi * i / n), z1)) for i in range(n)]
    else:
        corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        back = [bm.verts.new((x, y, 0.0)) for x, y in corners]
        front = [bm.verts.new((mouth * x, mouth * y, z1)) for x, y in corners]
    n = len(back)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((back[i], back[j], front[j], front[i]))  # open at both ends, like a real snoot
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
    else:
        sy = "(sy if shape == 1 or shape == 3 else sx)"
        exprs = ["max(sx, %g) * %g" % (AREA_MIN_SIZE, AREA_BACK),
                 "max(%s, %g) * %g" % (sy, AREA_MIN_SIZE, AREA_BACK),
                 "max(sx, %s, %g) * %g" % (sy, AREA_MIN_SIZE, AREA_BACK)]  # length follows the larger side
        names = {"sx": "size", "sy": "size_y", "shape": "shape"}
    for i, expr in enumerate(exprs):
        d = obj.driver_add("scale", i).driver
        d.type = "SCRIPTED"
        for name, path in names.items():
            if name in expr:
                _var(d, name, light, path)
        d.expression = expr


def _clamp(v, lo, hi):
    return max(lo, min(hi, float(v)))


def add(light_obj, length=None, mouth=None):
    """Create (or refit) the managed snoot for a spot or area light object."""
    light = light_obj.data
    if not supported(light):
        raise ValueError("Snoots work on spot and area lights.")
    if find_handmade(light_obj) is not None and find(light_obj) is None:
        raise ValueError("This light already has a hand-built snoot (%s). Convert it or remove it first."
                         % find_handmade(light_obj).name)
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
    _build_mesh(obj.data, obj["tla_round"], float(obj.tla_snoot_length), float(obj.tla_snoot_mouth))
    _drive(obj, light)
    return obj


def needs_refit(light_obj):
    obj = find(light_obj)
    return obj is not None and bool(obj.get("tla_round")) != is_round(light_obj.data)


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
    which then scales with the light. The original is hidden, not deleted."""
    src = find_handmade(light_obj)
    if src is None:
        raise ValueError("No hand-built snoot found on this light.")
    m = measure(light_obj, src)
    src.name = src.name + " (original)"
    src.hide_viewport = src.hide_render = True
    src.hide_set(True)
    src["tla_replaced"] = True
    obj = add(light_obj, length=m["length"], mouth=m["mouth"])
    return obj, m
