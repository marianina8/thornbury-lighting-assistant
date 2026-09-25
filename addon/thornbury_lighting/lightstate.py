# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading and writing a spot or area light's real data-block. Plain code, no AI.

Node wiring was verified against Blender 4.2.0, 4.5.14 LTS, 5.0.1, 5.1.2 and
5.2.2 LTS (bpy wheels) on 2026-09-24, including Cycles test renders:

* A light's node tree is Emission -> Light Output (Surface) once use_nodes is on.
* Gobo: Texture Coordinate "Normal" is the ray direction in the light's local
  space (confirmed by rotating the light). Image UV = xy / -z * k + 0.5, with
  k = 0.5 / tan(spot_size / 2) so the image fills the cone. Image Color ->
  Emission Color (multiplies the light's own colour).
* IES: IES Texture output -> Emission Strength. The output socket was renamed
  "Fac" (4.x) -> "Factor" (5.0+), so it is always linked by index, never name.
* Light node trees only render in Cycles (EEVEE ignores them).

The addon only ever edits a node tree that is Blender's default or one it built
itself; a hand-built tree is reported as "custom" and never touched.
"""

import json
import math
import os
import warnings

import bpy

from . import snoot

PRESET_DIR = os.path.join(os.path.dirname(__file__), "presets")
NODE_PREFIX = "TLA "
PRESET_KEY = "tla_preset"  # custom property on the Light: current preset id
PREV_NODES_KEY = "tla_prev_use_nodes"  # use_nodes before we switched it on

# Blender RNA hard limits (identical in 4.2 - 5.2) plus the backend's policy
# limits. The backend clamps first; these are the addon's own second check,
# applied to anything the artist edits in the diff before Apply.
SPOT_SIZE_MIN = math.radians(1.0)
SPOT_SIZE_MAX = math.pi
ENERGY_MAX = 1_000_000.0
TEMPERATURE_MIN, TEMPERATURE_MAX = 800.0, 20000.0
RADIUS_MAX = 100.0
AREA_SIZE_MIN, AREA_SIZE_MAX = 0.01, 100.0  # RNA: 0 - FLT_MAX (soft 100); 0 is degenerate
SPREAD_MIN, SPREAD_MAX = math.radians(1.0), math.pi  # RNA: 0 - 180 deg
SUPPORTED_TYPES = {"SPOT", "AREA"}


def load_library():
    with open(os.path.join(PRESET_DIR, "presets.json"), encoding="utf-8") as fh:
        return {p["id"]: p for p in json.load(fh)["presets"]}


LIBRARY = load_library()
USER_PREFIX = "user:"  # a gobo from the artist's own image: "user:<image name>"
USER_TAG = "tla_user_gobo"


def user_gobo_image(pid):
    if not pid.startswith(USER_PREFIX):
        return None
    img = bpy.data.images.get(pid[len(USER_PREFIX):])
    return img if img is not None and img.get(USER_TAG) else None


def resolve(pid):
    """The preset dict for a library id or a user gobo, or None."""
    if pid in LIBRARY:
        return LIBRARY[pid]
    img = user_gobo_image(pid)
    if img is not None:
        return {"id": pid, "kind": "gobo", "label": img.name, "image": img, "extension": "EXTEND"}
    return None


def has_temperature(light):
    return "temperature" in light.bl_rna.properties and "use_temperature" in light.bl_rna.properties


# --------------------------------------------------------------------- state

def _our_nodes(nt):
    return [n for n in nt.nodes if n.name.startswith(NODE_PREFIX)]


def _emission_and_output(nt):
    em = [n for n in nt.nodes if n.bl_idname == "ShaderNodeEmission" and not n.name.startswith(NODE_PREFIX)]
    out = [n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputLight"]
    return (em[0] if len(em) == 1 else None), (out[0] if len(out) == 1 else None)


def _is_default_tree(nt):
    """Blender's default light tree: just Emission -> Light Output."""
    em, out = _emission_and_output(nt)
    if em is None or out is None or len(nt.nodes) != 2:
        return False
    links = list(nt.links)
    return (len(links) == 1 and links[0].from_node == em and links[0].to_node == out
            and not em.inputs[0].is_linked and not em.inputs[1].is_linked)


def _is_our_tree(nt):
    """Default tree plus only our own nodes feeding the Emission node."""
    ours = _our_nodes(nt)
    if not ours:
        return False
    em, out = _emission_and_output(nt)
    if em is None or out is None or len(nt.nodes) != 2 + len(ours):
        return False
    for link in nt.links:
        if link.from_node == em and link.to_node == out:
            continue
        if not link.from_node.name.startswith(NODE_PREFIX):
            return False
        if link.to_node != em and not link.to_node.name.startswith(NODE_PREFIX):
            return False
    return True


def preset_state(light):
    """'none', an addon preset id, or 'custom' (a hand-built node tree)."""
    nt = light.node_tree
    if nt is None:
        return "none"
    if _is_our_tree(nt):
        pid = light.get(PRESET_KEY, "")
        return pid if resolve(pid) is not None else "custom"
    if _is_default_tree(nt) or len(nt.nodes) == 0:
        return "none"
    return "custom"


def read_state(light, obj=None):
    """The light's real current values, in the backend's schema. `obj` is the
    light's object (needed for the snoot, which is a child object)."""
    state = {
        "name": light.name,
        "type": light.type,
        "energy": float(light.energy),
        "color": [float(c) for c in light.color],
        "preset": preset_state(light) if light.type == "SPOT" else "none",
    }
    if light.type == "SPOT":
        state.update({
            "spot_size": float(light.spot_size),
            "spot_blend": float(light.spot_blend),
            "shadow_soft_size": float(light.shadow_soft_size),
            "use_square": bool(light.use_square),
        })
    elif light.type == "AREA":
        state.update({
            "size": float(light.size),
            "size_y": float(light.size_y),
            "shape": light.shape,
            "spread": float(light.spread),
        })
    if has_temperature(light):
        state["use_temperature"] = bool(light.use_temperature)
        state["temperature"] = float(light.temperature)
    if obj is not None and snoot.supported(light):
        state.update(snoot.state(obj))
    return state


def capabilities(light):
    return {"temperature": has_temperature(light)}


# --------------------------------------------------------------------- apply

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def sanitize(values, light):
    """Second, client-side range check (the backend already clamped). Drops
    unknown fields and anything this light type or Blender doesn't support."""
    out = {}
    spot, area = light.type == "SPOT", light.type == "AREA"
    if spot and "spot_size" in values:
        out["spot_size"] = _clamp(float(values["spot_size"]), SPOT_SIZE_MIN, SPOT_SIZE_MAX)
    if spot and "spot_blend" in values:
        out["spot_blend"] = _clamp(float(values["spot_blend"]), 0.0, 1.0)
    if "energy" in values:
        out["energy"] = _clamp(float(values["energy"]), 0.0, ENERGY_MAX)
    if "color" in values:
        out["color"] = [_clamp(float(c), 0.0, 1.0) for c in values["color"]][:3]
    if spot and "shadow_soft_size" in values:
        out["shadow_soft_size"] = _clamp(float(values["shadow_soft_size"]), 0.0, RADIUS_MAX)
    if spot and "use_square" in values:
        out["use_square"] = bool(values["use_square"])
    if area:
        for f in ("size", "size_y"):
            if f == "size_y" and light.shape not in {"RECTANGLE", "ELLIPSE"}:
                continue  # Blender ignores size_y for square and disk lights
            if f in values:
                out[f] = _clamp(float(values[f]), AREA_SIZE_MIN, AREA_SIZE_MAX)
        if "spread" in values:
            out["spread"] = _clamp(float(values["spread"]), SPREAD_MIN, SPREAD_MAX)
    if has_temperature(light):
        if "temperature" in values:
            out["temperature"] = _clamp(float(values["temperature"]), TEMPERATURE_MIN, TEMPERATURE_MAX)
        if "use_temperature" in values:
            out["use_temperature"] = bool(values["use_temperature"])
    if spot and "preset" in values and (values["preset"] == "none" or resolve(values["preset"]) is not None):
        out["preset"] = values["preset"]
    if snoot.supported(light):
        if "snoot" in values:
            out["snoot"] = bool(values["snoot"])
        if "snoot_length" in values:
            out["snoot_length"] = _clamp(float(values["snoot_length"]), *snoot.LENGTH_RANGE)
        if "snoot_mouth" in values:
            out["snoot_mouth"] = _clamp(float(values["snoot_mouth"]), *snoot.MOUTH_RANGE)
    return out


SCALARS = ("spot_size", "spot_blend", "energy", "shadow_soft_size", "use_square", "temperature", "use_temperature",
           "size", "size_y", "spread")


def apply_values(light, values, obj=None):
    """Write values into the light data-block, all or nothing. Call from an
    operator with the UNDO flag so a single Ctrl+Z reverts the whole suggestion.

    Everything that can fail (preset checks, loading the image or IES file) is
    done before the first write; if anything still fails, every value and the
    previous preset are put back before the error is raised."""
    if light.type not in SUPPORTED_TYPES:
        raise ValueError("Only spot and area lights are supported.")
    v = sanitize(values, light)
    snoot_change = any(k in v for k in ("snoot", "snoot_length", "snoot_mouth"))
    if snoot_change:
        if obj is None or obj.data != light:
            raise ValueError("A snoot needs the light's object.")
        if snoot.find_handmade(obj) is not None and snoot.find(obj) is None:
            raise ValueError("This light has a hand-built snoot; convert it in the panel first.")
    before_preset = preset_state(light)
    new_preset = v.get("preset")
    if new_preset == before_preset:
        new_preset = None
    if new_preset is not None:
        if before_preset == "custom":
            raise ValueError("This light has a hand-built node tree; the assistant won't rewire it.")
        if new_preset != "none":
            _check_tree_buildable(light)
            _resource(resolve(new_preset))  # load the file now, so a missing file fails before any write

    snapshot = {f: getattr(light, f) for f in SCALARS if hasattr(light, f)}
    snapshot_color = tuple(light.color)
    snoot_before = snoot.state(obj) if snoot_change else None
    try:
        for field in SCALARS:
            if field in v:
                setattr(light, field, v[field])
        if "color" in v:
            light.color = v["color"]
        if new_preset is not None:
            set_preset(light, new_preset)
        elif light.type == "SPOT" and resolve(preset_state(light)) is not None:
            _update_gobo_scale(light)  # keep a gobo filling the (possibly new) cone
        if snoot_change:
            _apply_snoot(obj, v)
    except Exception:
        for f, val in snapshot.items():
            setattr(light, f, val)
        light.color = snapshot_color
        if snoot_change:
            _restore_snoot(obj, snoot_before)
        try:
            set_preset(light, before_preset if resolve(before_preset) is not None else "none")
        except Exception:
            pass
        raise
    return v


def _apply_snoot(obj, v):
    want = v.get("snoot")
    if want is False:
        snoot.remove(obj)
    elif want is True or snoot.find(obj) is not None:
        snoot.add(obj, length=v.get("snoot_length"), mouth=v.get("snoot_mouth"))
    else:  # length/mouth without a snoot: adding one is implied
        snoot.add(obj, length=v.get("snoot_length"), mouth=v.get("snoot_mouth"))


def _restore_snoot(obj, before):
    try:
        if before.get("snoot") and "snoot_length" in before:
            snoot.add(obj, length=before["snoot_length"], mouth=before["snoot_mouth"])
        elif not before.get("snoot"):
            snoot.remove(obj)
    except Exception:
        pass


# ------------------------------------------------------------------- presets

def _check_tree_buildable(light):
    nt = light.node_tree
    if nt is None or len(nt.nodes) == 0:
        return  # Blender creates (or we add) the default nodes
    em, out = _emission_and_output(nt)
    if em is None or out is None:
        raise ValueError("The light's node tree has no single Emission -> Light Output pair.")


def _ensure_default_nodes(nt):
    """An emptied light tree gets Blender's default Emission -> Light Output."""
    if len(nt.nodes) == 0:
        em = nt.nodes.new("ShaderNodeEmission")
        out = nt.nodes.new("ShaderNodeOutputLight")
        out.location = (300, 0)
        nt.links.new(em.outputs[0], out.inputs[0])


def _resource(preset):
    return _image(preset) if preset["kind"] == "gobo" else _ies_text(preset)


def _remove_our_nodes(light):
    nt = light.node_tree
    if nt is None:
        return
    for n in _our_nodes(nt):
        nt.nodes.remove(n)


def _use_nodes(light):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return bool(light.use_nodes)


def _set_use_nodes(light, value):
    """Blender 5.1+ creates lights with nodes already on and 5.2 deprecates
    Light.use_nodes (removal planned for 6.0), so only write it when it has to
    change, and never let the deprecation warning reach the artist."""
    if _use_nodes(light) == bool(value):
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        light.use_nodes = value


def set_preset(light, preset_id):
    state = preset_state(light)
    if state == "custom":
        raise ValueError("This light has a hand-built node tree; the assistant won't rewire it.")
    if preset_id == "none":
        _remove_our_nodes(light)
        if PREV_NODES_KEY in light:
            _set_use_nodes(light, bool(light[PREV_NODES_KEY]))
            del light[PREV_NODES_KEY]
        if PRESET_KEY in light:
            del light[PRESET_KEY]
        return
    preset = resolve(preset_id)
    if preset is None:
        raise ValueError("Unknown gobo or profile: %s" % preset_id)
    if PREV_NODES_KEY not in light:
        light[PREV_NODES_KEY] = _use_nodes(light)
    _set_use_nodes(light, True)
    _remove_our_nodes(light)
    _ensure_default_nodes(light.node_tree)
    em, out = _emission_and_output(light.node_tree)
    if em is None or out is None:
        raise ValueError("The light's node tree has no single Emission -> Light Output pair.")
    if preset["kind"] == "gobo":
        _build_gobo(light, em, preset)
    else:
        _build_ies(light, em, preset)
    light[PRESET_KEY] = preset_id


def _node(nt, idname, name, loc):
    n = nt.nodes.new(idname)
    n.name = n.label = NODE_PREFIX + name
    n.location = loc
    return n


def _gobo_scale(spot_size):
    return 0.5 / math.tan(max(spot_size, SPOT_SIZE_MIN) / 2.0)


def _update_gobo_scale(light):
    nt = light.node_tree
    if nt is None:
        return
    k = _gobo_scale(light.spot_size)
    for axis in ("U", "V"):
        n = nt.nodes.get(NODE_PREFIX + "Gobo Scale " + axis)
        if n is not None:
            n.inputs[1].default_value = k


def _image(preset):
    if "image" in preset:  # the artist's own gobo, already in the file
        return preset["image"]
    name = NODE_PREFIX + preset["id"]
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.load(os.path.join(PRESET_DIR, preset["file"]), check_existing=False)
        img.name = name
        img.pack()  # the .blend keeps working on another machine
    img.colorspace_settings.name = "Non-Color"  # grey values are transmission, not colour
    return img


def _build_gobo(light, em, preset):
    nt = light.node_tree
    x, y = em.location
    coord = _node(nt, "ShaderNodeTexCoord", "Gobo Coord", (x - 1100, y))
    sep = _node(nt, "ShaderNodeSeparateXYZ", "Gobo Separate", (x - 900, y))
    negz = _node(nt, "ShaderNodeMath", "Gobo Depth", (x - 720, y - 160))
    negz.operation = "MULTIPLY"
    negz.inputs[1].default_value = -1.0
    comb = _node(nt, "ShaderNodeCombineXYZ", "Gobo UV", (x - 360, y))
    xf = _node(nt, "ShaderNodeMapping", "Gobo Transform", (x - 360, y - 220))  # rotate / size / offset
    xf.vector_type = "POINT"
    centre = _node(nt, "ShaderNodeVectorMath", "Gobo Center", (x - 200, y - 220))
    centre.operation = "ADD"
    centre.inputs[1].default_value = (0.5, 0.5, 0.0)
    tex = _node(nt, "ShaderNodeTexImage", "Gobo Image", (x - 200, y + 40))
    tex.image = _image(preset)
    tex.extension = preset.get("extension", "EXTEND")
    tex.interpolation = "Cubic"
    L = nt.links
    L.new(coord.outputs["Normal"], sep.inputs[0])
    L.new(sep.outputs[2], negz.inputs[0])
    k = _gobo_scale(light.spot_size)
    for i, axis in enumerate(("U", "V")):
        div = _node(nt, "ShaderNodeMath", "Gobo Project " + axis, (x - 720, y + 120 - 140 * i))
        div.operation = "DIVIDE"
        L.new(sep.outputs[i], div.inputs[0])
        L.new(negz.outputs[0], div.inputs[1])
        mad = _node(nt, "ShaderNodeMath", "Gobo Scale " + axis, (x - 540, y + 120 - 140 * i))
        mad.operation = "MULTIPLY_ADD"
        mad.inputs[1].default_value = k
        mad.inputs[2].default_value = 0.0  # centred; the transform then shifts to image space
        L.new(div.outputs[0], mad.inputs[0])
        L.new(mad.outputs[0], comb.inputs[i])
    L.new(comb.outputs[0], xf.inputs["Vector"])
    L.new(xf.outputs[0], centre.inputs[0])
    L.new(centre.outputs[0], tex.inputs[0])
    L.new(tex.outputs[0], em.inputs[0])  # Color
    update_gobo_transform(light)


def update_gobo_transform(light):
    """Apply the light's gobo rotation, size and offset to the Mapping node.
    Rotation turns the pattern about the beam axis; size 2 makes it twice as
    big; offset slides it (in pattern widths)."""
    nt = light.node_tree
    xf = nt.nodes.get(NODE_PREFIX + "Gobo Transform") if nt is not None else None
    if xf is None:
        return False
    size = max(0.05, float(getattr(light, "tla_gobo_size", 1.0)))
    off = getattr(light, "tla_gobo_offset", (0.0, 0.0))
    xf.inputs["Scale"].default_value = (1.0 / size, 1.0 / size, 1.0)
    xf.inputs["Rotation"].default_value = (0.0, 0.0, -float(getattr(light, "tla_gobo_rotation", 0.0)))
    xf.inputs["Location"].default_value = (-float(off[0]), -float(off[1]), 0.0)
    return True


def gobo_transform_changed(light):
    """Property update: files from 0.1/0.2 have no transform node, so rebuild
    their gobo once (same pattern) to add it."""
    if light.type != "SPOT":
        return
    if not update_gobo_transform(light):
        pid = preset_state(light)
        pre = resolve(pid)
        if pre is not None and pre["kind"] == "gobo":
            set_preset(light, pid)


def _ies_text(preset):
    name = NODE_PREFIX + preset["file"]
    txt = bpy.data.texts.get(name)
    if txt is None:
        txt = bpy.data.texts.new(name)
        with open(os.path.join(PRESET_DIR, preset["file"]), encoding="ascii") as fh:
            txt.write(fh.read())
    return txt


def _build_ies(light, em, preset):
    nt = light.node_tree
    x, y = em.location
    ies = _node(nt, "ShaderNodeTexIES", "IES Profile", (x - 260, y - 60))
    ies.mode = "INTERNAL"  # stored in the .blend as a text block, so it travels with the file
    ies.ies = _ies_text(preset)
    nt.links.new(ies.outputs[0], em.inputs[1])  # by index: "Fac" in 4.x, "Factor" in 5.x -> Strength
