# SPDX-License-Identifier: GPL-3.0-or-later
"""The physical gobo card: how a gobo shows up in EEVEE and Material Preview.

A gobo on the light itself (a texture in the light's node tree, lightstate.py)
is crisp and correct, but only Cycles renders light textures. So every light
with a gobo also gets a "card": a thin see-through plane parented just in
front of the lamp, with the gobo image as its mask, like the metal stencil in
a real fixture. It casts the pattern by blocking light, which EEVEE can do.

Only one of the two is ever active, chosen by the scene's render engine:
EEVEE (and Workbench) show the card; Cycles hides it and uses the light's own
texture, so a Cycles render never gets the pattern twice. A handler keeps this
in step when the engine changes (sync_engine).

Verified with EEVEE and Cycles renders on Blender 4.2 and 5.2 (bpy wheels):
the card needs the material's transparent shadows on, and is hidden from
camera, reflection and diffuse rays so only its shadow is seen.
"""

import math

import bpy

CARD_TAG = "tla_gobo_card"       # on the card object
MAT_KEY = "tla_card_material"    # on the Light: its card material (ID pointer)
MESH_NAME = "TLA Gobo Card"
MARGIN = 1.25                    # the card overhangs the cone; outside the image it blocks
MIN_DISTANCE = 0.5               # metres in front of the lamp
RADIUS_FACTOR = 4.0              # ...or this many light radii, whichever is further
MAX_HALF_ANGLE = 1.45            # radians: caps the card size for very wide cones


def _unit_mesh():
    me = bpy.data.meshes.get(MESH_NAME)
    if me is not None and me.get(CARD_TAG):
        return me
    me = bpy.data.meshes.new(MESH_NAME)
    m = MARGIN
    me.from_pydata([(-m, -m, 0), (m, -m, 0), (m, m, 0), (-m, m, 0)], [], [(0, 1, 2, 3)])
    uv = me.uv_layers.new(name="UVMap")
    for i, (x, y) in enumerate([(-m, -m), (m, -m), (m, m), (-m, m)]):
        uv.data[i].uv = (x * 0.5, y * 0.5)  # centred: +-1 on the card = the edge of the cone
    me[CARD_TAG] = True
    return me


def find(light_obj):
    for ch in light_obj.children:
        if ch.get(CARD_TAG):
            return ch
    return None


def material(light, image):
    """The card material for this light (one per light data-block)."""
    mat = light.get(MAT_KEY)
    if not isinstance(mat, bpy.types.Material):
        mat = bpy.data.materials.new("TLA Gobo Card - %s" % light.name)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        uv = nt.nodes.new("ShaderNodeTexCoord")
        uv.location = (-900, 0)
        xf = nt.nodes.new("ShaderNodeMapping")
        xf.name = "Gobo Transform"
        xf.vector_type = "POINT"
        xf.location = (-700, 0)
        centre = nt.nodes.new("ShaderNodeVectorMath")
        centre.operation = "ADD"
        centre.inputs[1].default_value = (0.5, 0.5, 0.0)
        centre.location = (-500, 0)
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.name = "Gobo Image"
        tex.extension = "CLIP"  # outside the image: black, so the card blocks there
        tex.interpolation = "Cubic"
        tex.location = (-300, 0)
        # White lets light through, black blocks it, colour tints it (in Cycles).
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        tr.location = (0, 0)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (200, 0)
        L = nt.links
        L.new(uv.outputs["UV"], xf.inputs["Vector"])
        L.new(xf.outputs[0], centre.inputs[0])
        L.new(centre.outputs[0], tex.inputs[0])
        L.new(tex.outputs[0], tr.inputs[0])
        L.new(tr.outputs[0], out.inputs["Surface"])
        for attr, value in (("use_transparent_shadow", True), ("surface_render_method", "DITHERED"),
                            ("blend_method", "HASHED"), ("shadow_method", "HASHED")):
            if hasattr(mat, attr):
                try:
                    setattr(mat, attr, value)
                except (TypeError, AttributeError):
                    pass
        mat.diffuse_color = (0.0, 0.0, 0.0, 0.3)
        light[MAT_KEY] = mat
    mat.node_tree.nodes["Gobo Image"].image = image
    update_transform(light)
    return mat


def update_transform(light):
    """Same rotation / size / offset as the light's own gobo (lightstate.update_gobo_transform)."""
    mat = light.get(MAT_KEY)
    if not isinstance(mat, bpy.types.Material) or mat.node_tree is None:
        return
    xf = mat.node_tree.nodes.get("Gobo Transform")
    if xf is None:
        return
    size = max(0.05, float(getattr(light, "tla_gobo_size", 1.0)))
    off = getattr(light, "tla_gobo_offset", (0.0, 0.0))
    xf.inputs["Scale"].default_value = (1.0 / size, 1.0 / size, 1.0)
    xf.inputs["Rotation"].default_value = (0.0, 0.0, -float(getattr(light, "tla_gobo_rotation", 0.0)))
    xf.inputs["Location"].default_value = (-float(off[0]), -float(off[1]), 0.0)


def _drive_scale(card, light):
    """Card half-width = distance * tan(half the cone), so it always fills the
    beam. The distance is baked into the expression: reading the card's own
    location would make a dependency cycle."""
    dist = abs(card.location.z)
    for i in range(2):
        try:
            card.driver_remove("scale", i)
        except TypeError:
            pass
        d = card.driver_add("scale", i).driver
        d.type = "SCRIPTED"
        v = d.variables.new()
        v.name, v.type = "s", "SINGLE_PROP"
        v.targets[0].id_type = "LIGHT"
        v.targets[0].id = light
        v.targets[0].data_path = "spot_size"
        d.expression = "%.6g * tan(min(s / 2, %g))" % (dist, MAX_HALF_ANGLE)


def add(light_obj, image):
    """Create (or refresh) the card for one light object."""
    light = light_obj.data
    mat = material(light, image)
    card = find(light_obj)
    if card is None:
        card = bpy.data.objects.new("%s Gobo Card" % light_obj.name, _unit_mesh())
        card[CARD_TAG] = True
        for coll in light_obj.users_collection:
            coll.objects.link(card)
        card.parent = light_obj
        dist = max(MIN_DISTANCE, RADIUS_FACTOR * float(light.shadow_soft_size))
        card.location = (0.0, 0.0, -dist)  # a light shines down its local -Z
        card.hide_select = True
        card.display_type = "WIRE"
        for vis in ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission",
                    "visible_volume_scatter"):
            if hasattr(card, vis):
                setattr(card, vis, False)
        _drive_scale(card, light)
    if not card.material_slots:
        card.data.materials.append(None)
    card.material_slots[0].link = "OBJECT"
    card.material_slots[0].material = mat
    _apply_engine(card, _engine_for(card))
    return card


def remove(light_obj):
    card = find(light_obj)
    if card is not None:
        bpy.data.objects.remove(card, do_unlink=True)


def drop_material(light):
    mat = light.get(MAT_KEY)
    if MAT_KEY in light:
        del light[MAT_KEY]
    if isinstance(mat, bpy.types.Material) and mat.users == 0:
        bpy.data.materials.remove(mat)


def sync(light, image):
    """Every object using this light gets a card when it has a gobo (image),
    and loses it when it doesn't."""
    objs = [o for o in bpy.data.objects if o.type == "LIGHT" and o.data == light]
    if image is None:
        for o in objs:
            remove(o)
        drop_material(light)
        return
    for o in objs:
        add(o, image)


# ------------------------------------------------ which engine shows what

def _engine_for(card):
    scenes = card.users_scene or (bpy.context.scene,)
    return scenes[0].render.engine if scenes and scenes[0] is not None else "CYCLES"


def _has_image(card):
    mat = card.material_slots[0].material if card.material_slots else None
    tex = mat.node_tree.nodes.get("Gobo Image") if mat is not None and mat.node_tree is not None else None
    return tex is not None and tex.image is not None


def _apply_engine(card, engine):
    # Cycles uses the light's own texture: crisp, and never doubled. A card
    # whose image was deleted would block all light, so it hides too.
    hide = engine == "CYCLES" or not _has_image(card)
    if card.hide_render != hide:
        card.hide_render = hide
    if card.hide_viewport != hide:
        card.hide_viewport = hide


_last_engine = {}


def sync_engine(*_args):
    """Handler: show cards under EEVEE/Workbench, hide them under Cycles. Also
    tidies cards whose light was deleted or no longer has a gobo."""
    changed = False
    for sc in bpy.data.scenes:
        if _last_engine.get(sc.name) != sc.render.engine:
            _last_engine[sc.name] = sc.render.engine
            changed = True
    if not changed:
        return
    for ob in [o for o in bpy.data.objects if o.get(CARD_TAG)]:
        parent = ob.parent
        if parent is None or parent.type != "LIGHT" or not isinstance(parent.data.get(MAT_KEY), bpy.types.Material):
            bpy.data.objects.remove(ob, do_unlink=True)
            continue
        _apply_engine(ob, _engine_for(ob))


@bpy.app.handlers.persistent
def _on_update(scene, depsgraph=None):
    sync_engine()


@bpy.app.handlers.persistent
def _on_load(*_args):
    from . import lightstate  # files from 0.3.2 and earlier have gobos but no cards yet
    for light in bpy.data.lights:
        try:
            lightstate.sync_card(light)
        except Exception as e:
            print("Thornbury: couldn't add a gobo card to %s: %s" % (light.name, e))
    _last_engine.clear()
    sync_engine()


@bpy.app.handlers.persistent
def _on_render(*_args):
    _last_engine.clear()  # e.g. `blender -b file.blend -E CYCLES`: the engine changed without a UI update
    sync_engine()


def register():
    if _on_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_update)
    if _on_render not in bpy.app.handlers.render_pre:
        bpy.app.handlers.render_pre.append(_on_render)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    for hl, fn in ((bpy.app.handlers.depsgraph_update_post, _on_update), (bpy.app.handlers.load_post, _on_load),
                   (bpy.app.handlers.render_pre, _on_render)):
        if fn in hl:
            hl.remove(fn)

