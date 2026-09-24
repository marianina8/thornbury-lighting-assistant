# SPDX-License-Identifier: GPL-3.0-or-later
"""Session state for the panel. It lives on the WindowManager, so it is never
saved into the artist's .blend file."""

import json
import math

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, StringProperty)

from . import lightstate

# field -> (label, kind). Order is the order rows appear in the diff.
FIELDS = {
    "spot_size": ("Cone angle", "ANGLE"),
    "spot_blend": ("Blend (edge softness)", "FACTOR"),
    "energy": ("Power", "POWER"),
    "color": ("Color", "COLOR"),
    "use_temperature": ("Use temperature", "BOOL"),
    "temperature": ("Temperature", "TEMP"),
    "shadow_soft_size": ("Radius (shadow softness)", "DISTANCE"),
    "use_square": ("Square cone", "BOOL"),
    "preset": ("Gobo / IES preset", "PRESET"),
}

PRESET_ITEMS = [("none", "None", "Remove the assistant's preset")] + [
    (p["id"], p["label"], p["use_when"]) for p in lightstate.LIBRARY.values()
]


class TLA_Row(bpy.types.PropertyGroup):
    field: StringProperty()
    label: StringProperty()
    kind: StringProperty()
    include: BoolProperty(name="Include", default=True, description="Untick to leave this setting as it is")
    original: StringProperty()  # JSON of the value the backend proposed
    v_angle: FloatProperty(name="Cone angle", subtype="ANGLE", min=lightstate.SPOT_SIZE_MIN, max=lightstate.SPOT_SIZE_MAX)
    v_factor: FloatProperty(name="Blend", min=0.0, max=1.0)
    v_power: FloatProperty(name="Power", unit="POWER", min=0.0, soft_max=lightstate.ENERGY_MAX)
    v_distance: FloatProperty(name="Radius", subtype="DISTANCE", min=0.0, max=lightstate.RADIUS_MAX)
    v_temp: FloatProperty(name="Kelvin", min=lightstate.TEMPERATURE_MIN, max=lightstate.TEMPERATURE_MAX)
    v_color: FloatVectorProperty(name="Color", subtype="COLOR", size=3, min=0.0, max=1.0)
    v_bool: BoolProperty(name="On")
    v_preset: EnumProperty(name="Preset", items=PRESET_ITEMS)

    _attr = {"ANGLE": "v_angle", "FACTOR": "v_factor", "POWER": "v_power", "DISTANCE": "v_distance",
             "TEMP": "v_temp", "COLOR": "v_color", "BOOL": "v_bool", "PRESET": "v_preset"}

    @property
    def attr(self):
        return self._attr[self.kind]

    def get(self):
        v = getattr(self, self.attr)
        return [float(c) for c in v] if self.kind == "COLOR" else v

    def set(self, value):
        setattr(self, self.attr, value)
        self.original = json.dumps(self.get())

    def edited(self):
        cur, orig = self.get(), json.loads(self.original)
        if isinstance(cur, list):
            return any(abs(a - b) > 1e-4 for a, b in zip(cur, orig))
        if isinstance(cur, float):
            return abs(cur - orig) > 1e-4 * max(1.0, abs(orig))
        return cur != orig


class TLA_State(bpy.types.PropertyGroup):
    note: StringProperty(name="Note", maxlen=500,
                         description="Plain-language lighting note, e.g. 'snoot the key so it stops spilling on the wall'")
    status: EnumProperty(items=[(s, s.title(), "") for s in ("IDLE", "WAITING", "READY", "NO_CHANGE", "OUT_OF_SCOPE", "ERROR")])
    message: StringProperty()
    request_id: StringProperty()
    token: StringProperty()  # identifies the in-flight Suggest; a result for any other token is ignored
    light_name: StringProperty()
    light_uid: IntProperty()
    snapshot: StringProperty()  # JSON: the light's state when the note was sent
    rationale: StringProperty()
    confidence: FloatProperty(subtype="FACTOR", min=0.0, max=1.0)
    adjustments: StringProperty()  # one per line
    usage: StringProperty()
    rows: CollectionProperty(type=TLA_Row)

    def clear(self, keep_note=True):
        note = self.note
        self.status, self.message, self.request_id, self.token = "IDLE", "", "", ""
        self.light_name, self.light_uid, self.snapshot = "", 0, ""
        self.rationale, self.confidence, self.adjustments = "", 0.0, ""
        self.rows.clear()
        if keep_note:
            self.note = note


def fill_rows(state, proposal, light):
    state.rows.clear()
    for field, (label, kind) in FIELDS.items():
        if field not in proposal:
            continue
        if field in ("temperature", "use_temperature") and not lightstate.has_temperature(light):
            continue
        row = state.rows.add()
        row.field, row.label, row.kind = field, label, kind
        row.set(proposal[field])


def format_value(field, value):
    if value is None:
        return "-"
    if field == "spot_size":
        return "%.1f°" % math.degrees(value)
    if field == "energy":
        return "%.4g W" % value
    if field == "temperature":
        return "%d K" % round(value)
    if field == "shadow_soft_size":
        return "%.3g m" % value
    if field == "spot_blend":
        return "%.3f" % value
    if field == "color":
        return "(%.2f, %.2f, %.2f)" % tuple(value)
    if field == "preset":
        return "None" if value == "none" else ("Custom nodes" if value == "custom" else lightstate.LIBRARY.get(value, {}).get("label", value))
    if isinstance(value, bool):
        return "On" if value else "Off"
    return str(value)


classes = (TLA_Row, TLA_State)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.tla = bpy.props.PointerProperty(type=TLA_State)
    # Re-enabling the addon mid-request must not leave the panel stuck on
    # "Asking…" (the old request's result can no longer arrive).
    try:
        for wm in bpy.data.window_managers:
            if wm.tla.status == "WAITING":
                wm.tla.clear()
    except AttributeError:
        pass  # bpy.data is restricted while Blender starts up; nothing is waiting then


def unregister():
    del bpy.types.WindowManager.tla
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
