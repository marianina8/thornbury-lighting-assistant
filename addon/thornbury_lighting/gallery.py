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

from . import lightstate

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
    return [img for img in bpy.data.images if img.get(lightstate.USER_TAG)]


def gobo_items(self, context):
    items = [("none", "No gobo", "Remove the gobo", _icon("none"), 0)]
    for i, p in enumerate(p for p in lightstate.LIBRARY.values() if p["kind"] == "gobo"):
        items.append((p["id"], p["label"], "%s: %s" % (p.get("family", "Gobo"), p["use_when"]), _icon(p["id"]), i + 1))
    for j, img in enumerate(_user_images()):
        icon = 0
        try:
            img.preview_ensure()
            icon = img.preview.icon_id
        except Exception:
            pass
        items.append((lightstate.USER_PREFIX + img.name, img.name, "Your own gobo image", icon, 1000 + j))
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


LIGHT_PROPS = {
    "tla_gobo_rotation": FloatProperty(name="Rotate", subtype="ANGLE", default=0.0, soft_min=-3.14159, soft_max=3.14159,
                                       description="Turn the gobo pattern about the beam", update=_transform_changed),
    "tla_gobo_size": FloatProperty(name="Size", default=1.0, min=0.1, max=10.0, soft_max=4.0,
                                   description="Pattern size (2 = twice as big)", update=_transform_changed),
    "tla_gobo_offset": FloatVectorProperty(name="Offset", size=2, default=(0.0, 0.0), soft_min=-1.0, soft_max=1.0,
                                           subtype="XYZ", description="Slide the pattern within the beam",
                                           update=_transform_changed),
}


def _active_spot(context):
    ob = context.object
    if ob is not None and ob.type == "LIGHT" and ob.data.type == "SPOT":
        return ob
    return None


class TLA_OT_gobo_use(bpy.types.Operator):
    bl_idname = "tla.gobo_use"
    bl_label = "Use"
    bl_description = "Put the selected gobo (or beam profile) on this light. Ctrl+Z reverts it"
    bl_options = {"REGISTER", "UNDO"}

    kind: StringProperty(default="gobo", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        ob = _active_spot(context)
        return ob is not None and lightstate.preset_state(ob.data) != "custom"

    def execute(self, context):
        wm, ob = context.window_manager, context.object
        pid = wm.tla_pick_gobo if self.kind == "gobo" else wm.tla_pick_ies
        try:
            lightstate.apply_values(ob.data, {"preset": pid}, ob)
        except Exception as e:
            self.report({"ERROR"}, "Not applied: %s" % e)
            return {"CANCELLED"}
        pre = lightstate.resolve(pid)
        self.report({"INFO"}, "%s on %s." % (pre["label"] if pre else "Nothing", ob.name))
        if pid != "none" and context.scene.render.engine != "CYCLES":
            self.report({"WARNING"}, "Gobos and profiles only render in Cycles.")
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
            img = bpy.data.images.load(self.filepath, check_existing=True)
        except RuntimeError as e:
            self.report({"ERROR"}, "Couldn't load that image: %s" % e)
            return {"CANCELLED"}
        img[lightstate.USER_TAG] = True
        if img.packed_file is None:
            img.pack()  # keep the .blend self-contained
        pid = lightstate.USER_PREFIX + img.name
        ob = context.object
        try:
            lightstate.apply_values(ob.data, {"preset": pid}, ob)
        except Exception as e:
            self.report({"ERROR"}, "Not applied: %s" % e)
            return {"CANCELLED"}
        context.window_manager.tla_pick_gobo = pid
        self.report({"INFO"}, "Added %s as a gobo." % img.name)
        return {"FINISHED"}


def draw(layout, context, ob):
    """The Gobo and Beam profile boxes (spot lights only)."""
    wm, light = context.window_manager, ob.data
    current = lightstate.preset_state(light)
    box = layout.box()
    box.label(text="Gobo", icon="IMAGE_RGB_ALPHA")
    if current == "custom":
        box.label(text="This light has a hand-built node tree; the picker won't rewire it.", icon="INFO")
        return
    box.template_icon_view(wm, "tla_pick_gobo", show_labels=True, scale=6.0, scale_popup=5.0)
    row = box.row(align=True)
    row.operator("tla.gobo_use", text="Use this gobo", icon="CHECKMARK").kind = "gobo"
    row.operator("tla.gobo_add_image", text="Add your own…", icon="FILE_IMAGE")
    pre = lightstate.resolve(current)
    if pre is not None and pre["kind"] == "gobo":
        box.label(text="On this light: %s" % pre["label"], icon="LIGHT_SPOT")
        col = box.column(align=True)
        col.prop(light, "tla_gobo_rotation")
        col.prop(light, "tla_gobo_size")
        col.row(align=True).prop(light, "tla_gobo_offset", text="")
    box2 = layout.box()
    box2.label(text="Beam profile (IES)", icon="LIGHT")
    box2.template_icon_view(wm, "tla_pick_ies", show_labels=True, scale=4.0, scale_popup=4.0)
    box2.operator("tla.gobo_use", text="Use this profile", icon="CHECKMARK").kind = "ies"
    if pre is not None and pre["kind"] == "ies":
        box2.label(text="On this light: %s" % pre["label"], icon="LIGHT_SPOT")
    if current != "none" and context.scene.render.engine != "CYCLES":
        layout.label(text="Gobos and profiles only render in Cycles.", icon="INFO")


classes = (TLA_OT_gobo_use, TLA_OT_gobo_add_image)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    for name, prop in LIGHT_PROPS.items():
        setattr(bpy.types.Light, name, prop)
    bpy.types.WindowManager.tla_pick_gobo = EnumProperty(name="Gobo", items=gobo_items)
    bpy.types.WindowManager.tla_pick_ies = EnumProperty(name="Beam profile", items=ies_items)
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    global _previews
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.WindowManager.tla_pick_gobo
    del bpy.types.WindowManager.tla_pick_ies
    for name in LIGHT_PROPS:
        delattr(bpy.types.Light, name)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
