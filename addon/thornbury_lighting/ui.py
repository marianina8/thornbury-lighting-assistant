# SPDX-License-Identifier: GPL-3.0-or-later
import json
import textwrap

import bpy

from . import lightstate, ops, props


def _wrapped(layout, text, width=48, icon="NONE"):
    col = layout.column(align=True)
    for i, line in enumerate(textwrap.wrap(text, width) or [""]):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def draw_assistant(layout, context):
    p, st = ops.prefs(context), ops.state(context)
    ob = context.object
    if ob is None or ob.type != "LIGHT":
        layout.label(text="Select a spot light.", icon="LIGHT_SPOT")
        return
    if ob.data.type != "SPOT":
        _wrapped(layout, "The assistant works on spot lights only (it adjusts the cone, blend, power, colour and radius).", icon="INFO")
        return
    if not p.backend_url.strip() or not p.api_key.strip():
        box = layout.box()
        _wrapped(box, "Add the backend URL and your API key in Edit > Preferences > Add-ons > Thornbury Lighting Assistant.", icon="PREFERENCES")
        return

    layout.prop(st, "note", text="", icon="GREASEPENCIL", placeholder="e.g. snoot the key so it stops spilling on the wall")
    row = layout.row()
    row.scale_y = 1.3
    row.operator("tla.suggest", icon="LIGHT_SPOT" if st.status != "WAITING" else "SORTTIME",
                 text="Asking…" if st.status == "WAITING" else "Suggest")

    if st.status == "ERROR":
        box = layout.box()
        _wrapped(box, st.message, icon="ERROR")
        box.operator("tla.discard", text="Dismiss")
    elif st.status in {"OUT_OF_SCOPE", "NO_CHANGE"}:
        box = layout.box()
        _wrapped(box, st.message or "Nothing to change.", icon="INFO")
        if st.rationale:
            _wrapped(box, st.rationale)
        box.operator("tla.discard", text="Dismiss")
    elif st.status == "READY":
        _draw_proposal(layout, context, st)

    if st.usage:
        layout.label(text=st.usage, icon="BLANK1")


def _draw_proposal(layout, context, st):
    light = ops.proposal_light(context)
    box = layout.box()
    head = box.row()
    head.label(text="Proposal for %s" % st.light_name, icon="LIGHT_SPOT")
    head.label(text="Confidence %d%%" % round(st.confidence * 100))
    if st.rationale:
        _wrapped(box, st.rationale, icon="INFO")
    if light is None:
        _wrapped(box, "Select %s again to apply this proposal." % st.light_name, icon="ERROR")

    snapshot = json.loads(st.snapshot or "{}")
    live = lightstate.read_state(light) if light is not None else {}
    changed_since = False
    grid = box.column(align=True)
    for r in st.rows:
        row = grid.row(align=True)
        row.prop(r, "include", text="")
        sub = row.row(align=True)
        sub.active = r.include
        split = sub.split(factor=0.42, align=True)
        split.label(text=r.label)
        cur = live.get(r.field, snapshot.get(r.field))
        if snapshot.get(r.field) != live.get(r.field) and light is not None:
            changed_since = True
        split2 = split.split(factor=0.45, align=True)
        split2.label(text=props.format_value(r.field, cur) + "  →")
        split2.prop(r, r.attr, text="")
        if r.edited():
            row.label(text="", icon="GREASEPENCIL")

    if st.adjustments:
        adj = box.column(align=True)
        adj.label(text="Corrected by the backend's range checks:", icon="MODIFIER")
        for line in st.adjustments.splitlines():
            _wrapped(adj, line, width=52, icon="DOT")
    if changed_since:
        _wrapped(box, "This light changed after you asked. Apply writes the proposed values shown.", icon="ERROR")
    if any(r.field == "preset" and r.include and r.v_preset != "none" for r in st.rows) and context.scene.render.engine != "CYCLES":
        _wrapped(box, "Gobo/IES presets only render in Cycles.", icon="INFO")

    row = box.row(align=True)
    row.scale_y = 1.2
    row.operator("tla.apply", text="Apply", icon="CHECKMARK")
    row.operator("tla.discard", text="Discard", icon="X")


class TLA_PT_light_properties(bpy.types.Panel):
    bl_label = "Lighting Note Assistant"
    bl_idname = "TLA_PT_light_properties"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return context.light is not None

    def draw(self, context):
        draw_assistant(self.layout, context)


class TLA_PT_sidebar(bpy.types.Panel):
    bl_label = "Lighting Note Assistant"
    bl_idname = "TLA_PT_sidebar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Thornbury"

    def draw(self, context):
        draw_assistant(self.layout, context)


class TLA_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    backend_url: bpy.props.StringProperty(name="Backend URL", description="The URL you were given with your key")
    api_key: bpy.props.StringProperty(name="API key", subtype="PASSWORD", description="Your personal key (tla_…)")
    timeout: bpy.props.IntProperty(name="Timeout (s)", default=25, min=5, max=60)
    connection_status: bpy.props.StringProperty(options={"SKIP_SAVE"})

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "backend_url")
        layout.prop(self, "api_key")
        layout.prop(self, "timeout")
        row = layout.row()
        row.operator("tla.test_connection", icon="URL")
        if self.connection_status:
            row.label(text=self.connection_status)
        if not getattr(bpy.app, "online_access", True):
            _wrapped(layout, "Online access is off. The assistant needs it: Preferences > System > Network > Allow Online Access.", width=90, icon="ERROR")
        _wrapped(layout, "Your note and the selected light's settings (cone, blend, power, colour, radius, preset) are sent to "
                         "the backend and logged for an audit trail. Nothing else from your scene is sent, and no images.",
                 width=90, icon="INFO")


classes = (TLA_Preferences, TLA_PT_light_properties, TLA_PT_sidebar)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
