# SPDX-License-Identifier: GPL-3.0-or-later
"""Suggest / Apply / Discard. Nothing is ever applied without the artist
clicking Apply, whatever the confidence."""

import json
import uuid

import bpy

from . import client, jobs, lightstate, props

CLIENT_VERSION = "0.1.0"


def prefs(context=None):
    return (context or bpy.context).preferences.addons[__package__].preferences


def state(context=None):
    return (context or bpy.context).window_manager.tla


def active_spot(context):
    ob = context.object
    if ob is not None and ob.type == "LIGHT" and ob.data.type == "SPOT":
        return ob.data
    return None


def redraw():
    wm = bpy.context.window_manager
    for win in getattr(wm, "windows", []):
        for area in win.screen.areas:
            area.tag_redraw()


def proposal_light(context):
    """The light the current proposal was made for, if it's still selected."""
    st, light = state(context), active_spot(context)
    if light is None or light.name != st.light_name or light.session_uid != st.light_uid:
        return None
    return light


def _report_outcome(name, applied=None):
    st, p = state(), prefs()
    rid, url, key = st.request_id, p.backend_url, p.api_key
    if not rid:
        return

    def done(_res, err):
        if err is not None:
            print("Thornbury Lighting Assistant: couldn't record the outcome:", err)

    jobs.run(lambda: client.outcome(url, key, rid, name, applied), done)


class TLA_OT_suggest(bpy.types.Operator):
    bl_idname = "tla.suggest"
    bl_label = "Suggest"
    bl_description = "Send the note and this light's current settings to the assistant for a proposal you can review"

    @classmethod
    def poll(cls, context):
        return active_spot(context) is not None and state(context).status != "WAITING"

    def execute(self, context):
        st, p, light = state(context), prefs(context), active_spot(context)
        if not p.backend_url.strip() or not p.api_key.strip():
            self.report({"ERROR"}, "Set the backend URL and API key in the add-on's preferences first.")
            return {"CANCELLED"}
        if not getattr(bpy.app, "online_access", True):
            self.report({"ERROR"}, "Blender's online access is off. Turn on Preferences > System > Network > Allow Online Access.")
            return {"CANCELLED"}
        note = " ".join(st.note.split())
        if not note:
            self.report({"ERROR"}, "Type a lighting note first.")
            return {"CANCELLED"}

        current = lightstate.read_state(light)
        payload = {"note": note, "light": current, "capabilities": lightstate.capabilities(light),
                   "blender_version": bpy.app.version_string, "client_version": CLIENT_VERSION}
        st.clear()
        token = uuid.uuid4().hex
        st.status, st.message, st.token = "WAITING", "Asking the assistant…", token
        st.light_name, st.light_uid, st.snapshot = light.name, light.session_uid, json.dumps(current)
        url, key, timeout = p.backend_url, p.api_key, p.timeout
        jobs.run(lambda: client.suggest(url, key, payload, timeout=timeout),
                 lambda res, err: on_suggest_result(res, err, token))
        return {"FINISHED"}


def on_suggest_result(res, err, token):
    st = state()
    if st.status != "WAITING" or st.token != token:
        return  # cancelled, superseded, or from before a file load: never show it
    try:
        _fill_result(st, res, err)
    except Exception as e:  # never leave the panel stuck on "Asking…"
        st.status, st.message = "ERROR", "Couldn't read the backend's answer: %s" % e
    redraw()


def _fill_result(st, res, err):
    if err is not None:
        st.status = "ERROR"
        st.message = str(err)
        return
    st.request_id = res.get("request_id", "")
    st.rationale = res.get("rationale", "")
    st.confidence = float(res.get("confidence", 0.0))
    st.adjustments = "\n".join("%s: %s → %s (%s)" % (a["field"], a["requested"], a["result"], a["reason"]) for a in res.get("adjustments", []))
    u = res.get("usage") or {}
    if u:
        st.usage = "%d of %d suggestions used this month" % (u.get("used", 0), u.get("limit", 0))
    light = bpy.data.lights.get(st.light_name)
    if not res.get("in_scope", True):
        st.status, st.message = "OUT_OF_SCOPE", res.get("out_of_scope_reason", "")
    elif not res.get("proposal"):
        st.status, st.message = "NO_CHANGE", "No change proposed."
    elif light is None:
        st.status, st.message = "ERROR", "The light was deleted or renamed while waiting."
    else:
        props.fill_rows(st, res["proposal"], light)
        st.status, st.message = "READY", ""


class TLA_OT_apply(bpy.types.Operator):
    bl_idname = "tla.apply"
    bl_label = "Apply Lighting Suggestion"
    bl_description = "Write the ticked values into this light (one undo step: Ctrl+Z reverts it)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return state(context).status == "READY" and proposal_light(context) is not None

    def execute(self, context):
        st, light = state(context), proposal_light(context)
        values = {r.field: r.get() for r in st.rows if r.include}
        if not values:
            self.report({"WARNING"}, "Nothing ticked; use Discard instead.")
            return {"CANCELLED"}
        edited = any((not r.include) or r.edited() for r in st.rows)
        try:
            applied = lightstate.apply_values(light, values)  # all-or-nothing
        except Exception as e:
            self.report({"ERROR"}, "Not applied; the light's settings are unchanged: %s" % e)
            return {"CANCELLED"}
        _report_outcome("edited" if edited else "applied", applied)
        if "preset" in applied and applied["preset"] != "none" and context.scene.render.engine != "CYCLES":
            self.report({"WARNING"}, "Applied. Gobo/IES presets only render in Cycles.")
        else:
            self.report({"INFO"}, "Applied. Ctrl+Z reverts it.")
        st.clear()
        st.note = ""
        return {"FINISHED"}


class TLA_OT_discard(bpy.types.Operator):
    bl_idname = "tla.discard"
    bl_label = "Discard"
    bl_description = "Throw the proposal away (or stop waiting for one); the light is not changed"

    @classmethod
    def poll(cls, context):
        return state(context).status in {"WAITING", "READY", "NO_CHANGE", "OUT_OF_SCOPE", "ERROR"}

    def execute(self, context):
        st = state(context)
        if st.status == "READY":
            _report_outcome("discarded")
        st.clear()
        return {"FINISHED"}


class TLA_OT_test_connection(bpy.types.Operator):
    bl_idname = "tla.test_connection"
    bl_label = "Test Connection"
    bl_description = "Check the backend URL and API key (does not use a suggestion)"

    def execute(self, context):
        p = prefs(context)
        url, key = p.backend_url, p.api_key
        p.connection_status = "Checking…"

        def done(res, err):
            pr = prefs()
            if err is not None:
                pr.connection_status = "✗ " + str(err)
            else:
                u = res.get("usage", {})
                paused = "" if res.get("enabled", True) else " (assistant is paused)"
                pr.connection_status = "✓ Connected as %s: %d of %d used this month%s" % (
                    res.get("label", "?"), u.get("used", 0), u.get("limit", 0), paused)
            redraw()

        jobs.run(lambda: client.me(url, key), done)
        return {"FINISHED"}


classes = (TLA_OT_suggest, TLA_OT_apply, TLA_OT_discard, TLA_OT_test_connection)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
