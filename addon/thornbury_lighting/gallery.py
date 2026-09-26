# SPDX-License-Identifier: GPL-3.0-or-later
"""Pick a gobo or beam profile by looking at it: a thumbnail grid in the
panel. No model call, works offline, one undo step per pick.

Thumbnails (presets/thumbs/<id>.png) are real Cycles renders of each gobo
projected onto a wall through this addon's own node wiring
(tools/render_thumbs.py), so they show what the light will look like, not the
raw black-and-white image. Artists can add their own images as gobos too.
"""

import os

import bpy
import bpy.utils.previews
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from . import lightstate, snoot

THUMB_DIR = os.path.join(lightstate.PRESET_DIR, "thumbs")
_previews = None
_items = {"gobo": [], "ies": []}  # Blender needs Python to keep enum items alive


def _icon(pid):
    if _previews is None:
        return 0
    if pid not in _previews:
        path = os.path.join(THUMB_DIR, pid + ".png")
        if not os.path.exists(path):
            return 0
        _previews.load(pid, path, "IMAGE")
    return _previews[pid].icon_id


def _user_images():
    return [img for img in bpy.data.images if isinstance(img.get(lightstate.USER_TAG), str)]


def gobo_items(self, context):
    items = [("none", "No gobo", "Remove the gobo", _icon("none"), 0)]
    for i, p in enumerate(p for p in lightstate.LIBRARY.values() if p["kind"] == "gobo"):
        items.append((p["id"], p["label"], "%s: %s" % (p.get("family", "Gobo"), p["use_when"]), _icon(p["id"]), i + 1))
    for img in _user_images():
        icon = 0
        try:
            img.preview_ensure()
            icon = img.preview.icon_id
        except Exception:
            pass
        # A stable number per image, so a stored pick never shifts to another image.
        items.append((lightstate.user_gobo_id(img), img.name, "Your own gobo image", icon, int(img[lightstate.USER_NUM])))
    _items["gobo"] = items
    return items


def ies_items(self, context):
    items = [("none", "No profile", "Plain beam, no IES profile", _icon("none"), 0)]
    for i, p in enumerate(p for p in lightstate.LIBRARY.values() if p["kind"] == "ies"):
        items.append((p["id"], p["label"], p["use_when"], _icon(p["id"]), i + 1))
    _items["ies"] = items
    return items


def _transform_changed(self, context):
    lightstate.gobo_transform_changed(self)


# The pick lives on each light, not on the window, so the grid always shows
# what THIS light has, and clicking a thumbnail applies it straight away (the
# property change is its own undo step).
def _number(kind, pid):
    """The enum number for a preset id, worked out directly (not from a cached
    item list, which can be stale after an image is added or deleted)."""
    if pid == "none":
        return 0
    if kind == "gobo" and pid.startswith("user:"):
        img = lightstate.user_gobo_image(pid)
        return int(img[lightstate.USER_NUM]) if img is not None else 0
    for i, p in enumerate(p for p in lightstate.LIBRARY.values() if p["kind"] == kind):
        if p["id"] == pid:
            return i + 1
    return 0


def _current_of_kind(light, kind):
    pid = lightstate.preset_state(light)
    pre = lightstate.resolve(pid)
    return pid if pre is not None and pre["kind"] == kind else "none"


def _get_choice(kind):
    def get(self):
        return _number(kind, _current_of_kind(self, kind))
    return get


def _set_choice(kind):
    def set_(self, value):
        items = gobo_items(None, None) if kind == "gobo" else ies_items(None, None)
        pid = next((it[0] for it in items if it[4] == value), None)
        if pid is None or lightstate.preset_state(self) == "custom":
            return
        if pid == "none" and _current_of_kind(self, kind) == "none":
            return  # "No gobo" must not strip an IES profile (and vice versa)
        try:
            lightstate.apply_values(self, {"preset": pid})
        except Exception as e:  # a property setter can't report; say it in the console
            print("Thornbury: couldn't set %s: %s" % (pid, e))
    return set_


LIGHT_PROPS = {
    "tla_gobo_rotation": FloatProperty(name="Rotate", subtype="ANGLE", default=0.0, soft_min=-3.14159, soft_max=3.14159,
                                       description="Turn the gobo pattern about the beam", update=_transform_changed, options=set()),
    "tla_gobo_size": FloatProperty(name="Size", default=1.0, min=0.1, max=10.0, soft_max=4.0,
                                   description="Pattern size (2 = twice as big)", update=_transform_changed, options=set()),
    "tla_gobo_offset": FloatVectorProperty(name="Offset", size=2, default=(0.0, 0.0), soft_min=-1.0, soft_max=1.0,
                                           subtype="XYZ", description="Slide the pattern within the beam",
                                           update=_transform_changed, options=set()),
    "tla_gobo_choice": EnumProperty(name="Gobo", items=gobo_items, get=_get_choice("gobo"), set=_set_choice("gobo"),
                                    description="Click a gobo to put it on this light", options=set()),
    "tla_ies_choice": EnumProperty(name="Beam profile", items=ies_items, get=_get_choice("ies"), set=_set_choice("ies"),
                                   description="Click a beam profile to put it on this light", options=set()),
}


def _active_spot(context):
    ob = context.object
    if ob is not None and ob.type == "LIGHT" and ob.data.type == "SPOT":
        return ob
    return None


class TLA_OT_gobo_use(bpy.types.Operator):
    bl_idname = "tla.gobo_use"
    bl_label = "Use"
    bl_description = "Put a gobo (or beam profile) on the active light. Ctrl+Z reverts it"
    bl_options = {"REGISTER", "UNDO"}

    preset: StringProperty(default="none", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        ob = _active_spot(context)
        return ob is not None and lightstate.preset_state(ob.data) != "custom"

    def execute(self, context):
        ob = context.object
        pid = self.preset
        if pid != "none" and lightstate.resolve(pid) is None:
            self.report({"ERROR"}, "That gobo is no longer in this file; pick another.")
            return {"CANCELLED"}
        try:
            lightstate.apply_values(ob.data, {"preset": pid}, ob)
        except Exception as e:
            self.report({"ERROR"}, "Not applied: %s" % e)
            return {"CANCELLED"}
        pre = lightstate.resolve(pid)
        self.report({"INFO"}, "%s on %s." % (pre["label"] if pre else "Nothing", ob.name))
        return {"FINISHED"}


class TLA_OT_make_light_unique(bpy.types.Operator):
    bl_idname = "tla.make_light_unique"
    bl_label = "Make This Light Separate"
    bl_description = ("This light shares its settings with other lights (a linked duplicate), so a gobo or any "
                      "other change lands on all of them. Give this one its own copy")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and ob.type == "LIGHT" and ob.data.users > 1

    def execute(self, context):
        ob = context.object
        old = ob.data
        ob.data = old.copy()
        s = snoot.find(ob)
        if s is not None:
            snoot._drive(s, ob.data)  # the snoot follows this light's size, not the shared one
        self.report({"INFO"}, "%s now has its own settings." % ob.name)
        return {"FINISHED"}


def _viewport_shows_gobos(context):
    """Gobos are light node trees, which only Cycles renders. Material Preview
    and Solid never show them, and neither does EEVEE."""
    if context.scene.render.engine != "CYCLES":
        return False
    areas = [a for a in context.screen.areas if a.type == "VIEW_3D"] if context.screen else []
    return any(a.spaces.active.shading.type == "RENDERED" for a in areas)


class TLA_OT_preview_gobos(bpy.types.Operator):
    bl_idname = "tla.preview_gobos"
    bl_label = "Show Gobos in the Viewport"
    bl_description = "Switch the render engine to Cycles and the 3D view to Rendered shading, so gobos are visible"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.render.engine = "CYCLES"
        for a in (context.screen.areas if context.screen else []):
            if a.type == "VIEW_3D":
                a.spaces.active.shading.type = "RENDERED"
        return {"FINISHED"}


class TLA_OT_gobo_add_image(bpy.types.Operator, ImportHelper):
    bl_idname = "tla.gobo_add_image"
    bl_label = "Add Your Own Gobo"
    bl_description = "Use any image as a gobo (white lets light through, black blocks it; colour tints it)"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.bmp;*.webp", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        ob = _active_spot(context)
        return ob is not None and lightstate.preset_state(ob.data) != "custom"

    def execute(self, context):
        try:
            # Always a fresh datablock: never take over an image a material already uses.
            img = bpy.data.images.load(self.filepath, check_existing=False)
        except RuntimeError as e:
            self.report({"ERROR"}, "Couldn't load that image: %s" % e)
            return {"CANCELLED"}
        pid = lightstate.user_gobo_id(img)
        img.pack()  # keep the .blend self-contained
        ob = context.object
        try:
            lightstate.apply_values(ob.data, {"preset": pid}, ob)
        except Exception as e:
            bpy.data.images.remove(img)
            self.report({"ERROR"}, "Not applied: %s" % e)
            return {"CANCELLED"}
        self.report({"INFO"}, "Added %s as a gobo." % img.name)
        return {"FINISHED"}


def draw(layout, context, ob):
    """The Gobo and Beam profile boxes (spot lights only)."""
    light = ob.data
    current = lightstate.preset_state(light)
    box = layout.box()
    box.label(text="Gobo", icon="IMAGE_RGB_ALPHA")
    if current == "custom":
        box.label(text="This light has a hand-built node tree; the picker won't rewire it.", icon="INFO")
        return
    if light.users > 1:
        warn = box.column(align=True)
        warn.label(text="Shared with %d other light(s): changes apply to all." % (light.users - 1), icon="ERROR")
        warn.operator("tla.make_light_unique", icon="UNLINKED")
    box.template_icon_view(light, "tla_gobo_choice", show_labels=True, scale=6.0, scale_popup=5.0)
    box.operator("tla.gobo_add_image", text="Add your own…", icon="FILE_IMAGE")
    if current == "broken":
        box.label(text="This light's gobo image was deleted; pick a new one.", icon="ERROR")
    pre = lightstate.resolve(current)
    if pre is not None and pre["kind"] == "gobo":
        box.label(text="On %s: %s" % (ob.name, pre["label"]), icon="LIGHT_SPOT")
        col = box.column(align=True)
        col.prop(light, "tla_gobo_rotation")
        col.prop(light, "tla_gobo_size")
        col.row(align=True).prop(light, "tla_gobo_offset", text="")
    box2 = layout.box()
    box2.label(text="Beam profile (IES)", icon="LIGHT")
    box2.template_icon_view(light, "tla_ies_choice", show_labels=True, scale=4.0, scale_popup=4.0)
    if pre is not None and pre["kind"] == "ies":
        box2.label(text="On %s: %s" % (ob.name, pre["label"]), icon="LIGHT_SPOT")
    if not _viewport_shows_gobos(context):
        tip = layout.box()
        tip.label(text="Gobos only show in Cycles, Rendered view.", icon="INFO")
        tip.label(text="Material Preview, Solid and EEVEE can't display them.", icon="BLANK1")
        tip.operator("tla.preview_gobos", icon="SHADING_RENDERED")


classes = (TLA_OT_gobo_use, TLA_OT_gobo_add_image, TLA_OT_make_light_unique, TLA_OT_preview_gobos)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    for name, prop in LIGHT_PROPS.items():
        setattr(bpy.types.Light, name, prop)
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    global _previews
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    for name in LIGHT_PROPS:
        delattr(bpy.types.Light, name)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
