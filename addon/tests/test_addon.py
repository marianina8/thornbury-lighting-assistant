"""Headless tests for the Blender addon, run inside real Blender builds:

    <venv with the bpy wheel>/bin/python addon/tests/test_addon.py
    TLA_BACKEND=http://127.0.0.1:8787 TLA_KEY=tla_... python addon/tests/test_addon.py   # + end-to-end

The Makefile runs them against every Blender version installed (make addon-test).
"""
import importlib
import json
import math
import os
import sys
import unittest
import urllib.request

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ZIP = os.environ.get("TLA_ZIP") or os.path.join(ROOT, "dist", "thornbury_lighting-0.1.0.zip")
BACKEND = os.environ.get("TLA_BACKEND", "")
KEY = os.environ.get("TLA_KEY", "")
PKG = "bl_ext.user_default.thornbury_lighting"


def install_like_an_artist():
    """Install the built .zip the way Preferences > Add-ons > Install from Disk
    does, into the throwaway user profile given by BLENDER_USER_RESOURCES."""
    assert os.environ.get("BLENDER_USER_RESOURCES"), "run via `make addon-test` (isolated user profile)"
    assert os.path.exists(ZIP), "build the zip first: make addon-zip"
    res = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
    assert res == {"FINISHED"}, res
    assert PKG in bpy.context.preferences.addons, list(bpy.context.preferences.addons.keys())


install_like_an_artist()
# Headless Blender starts offline; artists turn this on in Preferences > System.
bpy.context.preferences.system.use_online_access = True
tla = importlib.import_module(PKG)
jobs = importlib.import_module(PKG + ".jobs")
lightstate = importlib.import_module(PKG + ".lightstate")
ops = importlib.import_module(PKG + ".ops")
props = importlib.import_module(PKG + ".props")
client = importlib.import_module(PKG + ".client")


def new_scene():
    for coll in (bpy.data.objects, bpy.data.lights, bpy.data.images, bpy.data.texts):
        for item in list(coll):
            coll.remove(item)
    sc = bpy.context.scene
    light = bpy.data.lights.new("Key", "SPOT")
    light.energy, light.spot_size, light.spot_blend = 1000.0, math.radians(45), 0.15
    ob = bpy.data.objects.new("Key", light)
    sc.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    ops.state().clear(keep_note=False)
    return light, ob


_real_report = ops._report_outcome


class AddonTests(unittest.TestCase):
    def setUp(self):
        self.light, self.ob = new_scene()
        p = ops.prefs()
        p.backend_url, p.api_key = BACKEND or "http://127.0.0.1:1", KEY or "tla_x"

    def tearDown(self):
        ops._report_outcome = _real_report

    # ---------------------------------------------------------------- state
    def test_read_state_matches_datablock(self):
        s = lightstate.read_state(self.light)
        self.assertEqual(s["type"], "SPOT")
        self.assertAlmostEqual(s["spot_size"], math.radians(45), places=5)
        self.assertEqual(s["preset"], "none")
        self.assertEqual("temperature" in s, bpy.app.version >= (4, 5, 0))
        self.assertEqual(lightstate.capabilities(self.light)["temperature"], bpy.app.version >= (4, 5, 0))
        json.dumps(s)  # serialisable

    def test_apply_values_and_client_side_clamps(self):
        v = lightstate.apply_values(self.light, {"spot_size": 99.0, "spot_blend": -1, "energy": -5, "color": [2, 0.5, -1],
                                                 "shadow_soft_size": 0.4, "use_square": True, "unknown": 1})
        self.assertAlmostEqual(self.light.spot_size, math.pi, places=5)
        self.assertEqual(self.light.spot_blend, 0.0)
        self.assertEqual(self.light.energy, 0.0)  # Blender itself would accept -5
        self.assertEqual(list(self.light.color), [1.0, 0.5, 0.0])
        self.assertTrue(self.light.use_square)
        self.assertNotIn("unknown", v)

    def test_temperature_only_where_supported(self):
        v = lightstate.apply_values(self.light, {"temperature": 3200, "use_temperature": True})
        if bpy.app.version >= (4, 5, 0):
            self.assertEqual(self.light.temperature, 3200)
            self.assertTrue(self.light.use_temperature)
        else:
            self.assertEqual(v, {})

    # -------------------------------------------------------------- presets
    def test_gobo_preset_wiring(self):
        lightstate.apply_values(self.light, {"preset": "gobo_window_blinds"})
        nt = self.light.node_tree
        self.assertTrue(self.light.use_nodes)
        self.assertEqual(lightstate.preset_state(self.light), "gobo_window_blinds")
        em = [n for n in nt.nodes if n.bl_idname == "ShaderNodeEmission"][0]
        self.assertTrue(em.inputs[0].is_linked)
        self.assertEqual(em.inputs[0].links[0].from_node.bl_idname, "ShaderNodeTexImage")
        img = nt.nodes["TLA Gobo Image"].image
        self.assertTrue(img.packed_file is not None)
        self.assertEqual(img.colorspace_settings.name, "Non-Color")
        k = nt.nodes["TLA Gobo Scale U"].inputs[1].default_value
        self.assertAlmostEqual(k, 0.5 / math.tan(self.light.spot_size / 2), places=4)
        # A later cone change keeps the gobo filling the cone.
        lightstate.apply_values(self.light, {"spot_size": math.radians(20)})
        self.assertAlmostEqual(nt.nodes["TLA Gobo Scale U"].inputs[1].default_value, 0.5 / math.tan(math.radians(10)), places=4)

    def test_ies_preset_links_by_index_and_embeds_profile(self):
        lightstate.apply_values(self.light, {"preset": "ies_narrow_spot"})
        nt = self.light.node_tree
        ies = nt.nodes["TLA IES Profile"]
        self.assertEqual(ies.mode, "INTERNAL")
        self.assertIn("IESNA:LM-63-2002", ies.ies.as_string())
        em = [n for n in nt.nodes if n.bl_idname == "ShaderNodeEmission"][0]
        self.assertEqual(em.inputs[1].links[0].from_node, ies)  # Strength
        self.assertEqual(em.inputs[1].links[0].from_socket.name, "Factor" if bpy.app.version >= (5, 0, 0) else "Fac")

    def test_switch_and_remove_presets_restore_default(self):
        before = self.light.use_nodes  # False before 5.1, True from 5.1 on
        lightstate.apply_values(self.light, {"preset": "gobo_leaf_breakup"})
        lightstate.apply_values(self.light, {"preset": "ies_wide_flood"})
        nt = self.light.node_tree
        self.assertFalse(any(n.name.startswith("TLA Gobo") for n in nt.nodes))
        self.assertEqual(lightstate.preset_state(self.light), "ies_wide_flood")
        lightstate.apply_values(self.light, {"preset": "none"})
        self.assertEqual(len(nt.nodes), 2)
        self.assertEqual(lightstate.preset_state(self.light), "none")
        self.assertEqual(self.light.use_nodes, before, "use_nodes should go back to what the artist had")
        self.assertNotIn(lightstate.PRESET_KEY, self.light)

    def test_custom_node_tree_is_never_touched(self):
        self.light.use_nodes = True
        nt = self.light.node_tree
        noise = nt.nodes.new("ShaderNodeTexNoise")
        em = [n for n in nt.nodes if n.bl_idname == "ShaderNodeEmission"][0]
        nt.links.new(noise.outputs[0], em.inputs[1])
        self.assertEqual(lightstate.preset_state(self.light), "custom")
        with self.assertRaises(ValueError):
            lightstate.apply_values(self.light, {"preset": "gobo_slot"})
        self.assertEqual(len(nt.nodes), 3)
        # Plain settings still apply to a light with custom nodes.
        lightstate.apply_values(self.light, {"energy": 500})
        self.assertEqual(self.light.energy, 500)

    def test_every_preset_file_loads(self):
        for pid in lightstate.LIBRARY:
            lightstate.apply_values(self.light, {"preset": pid})
            self.assertEqual(lightstate.preset_state(self.light), pid)
        lightstate.apply_values(self.light, {"preset": "none"})

    # ------------------------------------------------------------ operators
    def _ready(self, proposal):
        st = ops.state()
        st.clear()
        st.status, st.request_id = "READY", "0" * 24
        st.light_name, st.light_uid = self.light.name, self.light.session_uid
        st.snapshot = json.dumps(lightstate.read_state(self.light))
        props.fill_rows(st, proposal, self.light)
        return st

    def test_apply_operator_is_undoable_and_respects_unticked_rows(self):
        self.assertIn("UNDO", ops.TLA_OT_apply.bl_options)
        st = self._ready({"spot_size": math.radians(25), "energy": 600.0, "preset": "gobo_soft_iris"})
        [r for r in st.rows if r.field == "energy"][0].include = False
        reported = []
        ops._report_outcome = lambda name, applied=None: reported.append((name, applied))
        self.assertEqual(bpy.ops.tla.apply(), {"FINISHED"})
        self.assertAlmostEqual(self.light.spot_size, math.radians(25), places=5)
        self.assertEqual(self.light.energy, 1000.0)  # unticked -> untouched
        self.assertEqual(lightstate.preset_state(self.light), "gobo_soft_iris")
        self.assertEqual(reported[0][0], "edited")
        self.assertNotIn("energy", reported[0][1])
        self.assertEqual(ops.state().status, "IDLE")

    def test_apply_refuses_a_different_light(self):
        self._ready({"energy": 600.0})
        other = bpy.data.lights.new("Rim", "SPOT")
        ob = bpy.data.objects.new("Rim", other)
        bpy.context.scene.collection.objects.link(ob)
        bpy.context.view_layer.objects.active = ob
        self.assertFalse(ops.TLA_OT_apply.poll(bpy.context))

    def test_edited_value_reported_as_edited(self):
        st = self._ready({"energy": 600.0})
        st.rows[0].v_power = 700.0
        reported = []
        ops._report_outcome = lambda name, applied=None: reported.append((name, applied))
        bpy.ops.tla.apply()
        self.assertEqual(self.light.energy, 700.0)
        self.assertEqual(reported, [("edited", {"energy": 700.0})])

    def test_undo_reverts_apply(self):
        """Real undo needs Blender's undo system; headless it may be unavailable."""
        self._ready({"energy": 250.0})
        ops._report_outcome = lambda *a, **k: None
        try:
            bpy.ops.ed.undo_push(message="before")
        except RuntimeError as e:
            self.skipTest("no undo stack in background mode: %s" % e)
        bpy.ops.tla.apply()
        self.assertEqual(self.light.energy, 250.0)
        try:
            bpy.ops.ed.undo()
        except RuntimeError as e:
            self.skipTest("no undo stack in background mode: %s" % e)
        self.assertEqual(bpy.data.lights["Key"].energy, 1000.0)

    def test_suggest_refuses_clearly_when_offline(self):
        bpy.context.preferences.system.use_online_access = False
        try:
            ops.state().note = "tighten it"
            with self.assertRaisesRegex(RuntimeError, "Allow Online Access"):
                bpy.ops.tla.suggest()
            self.assertEqual(ops.state().status, "IDLE")
        finally:
            bpy.context.preferences.system.use_online_access = True

    def test_non_spot_lights_are_not_offered(self):
        self.light.type = "POINT"
        self.assertFalse(ops.TLA_OT_suggest.poll(bpy.context))

    # ---------------------------------------------------------------- e2e
    @unittest.skipUnless(BACKEND and KEY, "set TLA_BACKEND and TLA_KEY (cmd/local) for the end-to-end test")
    def test_end_to_end_against_backend(self):
        me = client.me(BACKEND, KEY)
        self.assertIn("usage", me)
        ops.state().note = "snoot the key down so it stops spilling on the background, keep it warm"
        self.assertEqual(bpy.ops.tla.suggest(), {"FINISHED"})
        self.assertEqual(ops.state().status, "WAITING")
        self.assertTrue(jobs.wait_all(30))
        st = ops.state()
        self.assertEqual(st.status, "READY", st.message)
        rid = st.request_id
        fields = {r.field for r in st.rows}
        self.assertIn("spot_size", fields)
        self.assertNotIn("color", fields)
        before = self.light.spot_size
        self.assertEqual(bpy.ops.tla.apply(), {"FINISHED"})
        self.assertLess(self.light.spot_size, before)
        self.assertTrue(jobs.wait_all(30))
        with urllib.request.urlopen(BACKEND + "/local/audit") as r:
            audit = {a["request_id"]: a for a in json.load(r)}
        self.assertEqual(audit[rid]["outcome"], "applied")
        self.assertEqual(audit[rid]["blender_version"], bpy.app.version_string)
        self.assertEqual(audit[rid]["status"], "proposed")

    @unittest.skipUnless(BACKEND and KEY, "needs cmd/local")
    def test_bad_key_is_shown_not_raised(self):
        ops.prefs().api_key = "tla_" + "a" * 52
        ops.state().note = "tighten it"
        bpy.ops.tla.suggest()
        jobs.wait_all(30)
        st = ops.state()
        self.assertEqual(st.status, "ERROR")
        self.assertIn("API key", st.message)


if __name__ == "__main__":
    print("Blender", bpy.app.version_string)
    result = unittest.main(argv=[sys.argv[0], "-v"], exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
