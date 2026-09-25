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
def _manifest_version():
    import re as _re
    with open(os.path.join(ROOT, "addon", "thornbury_lighting", "blender_manifest.toml"), encoding="utf-8") as fh:
        return _re.search(r'^version = "([^"]+)"', fh.read(), _re.M).group(1)


ZIP = os.environ.get("TLA_ZIP") or os.path.join(ROOT, "dist", "thornbury_lighting-%s.zip" % _manifest_version())
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


def _load_modules():
    global tla, jobs, lightstate, ops, props, client, snoot, gallery, _real_report
    tla = importlib.import_module(PKG)
    jobs = importlib.import_module(PKG + ".jobs")
    lightstate = importlib.import_module(PKG + ".lightstate")
    ops = importlib.import_module(PKG + ".ops")
    props = importlib.import_module(PKG + ".props")
    client = importlib.import_module(PKG + ".client")
    snoot = importlib.import_module(PKG + ".snoot")
    gallery = importlib.import_module(PKG + ".gallery")
    _real_report = ops._report_outcome


_load_modules()


def new_scene():
    for coll in (bpy.data.objects, bpy.data.lights, bpy.data.meshes, bpy.data.images, bpy.data.texts):
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

    # ------------------------------------------ regressions from the review
    def test_stale_result_never_fills_a_new_request(self):
        """Review #2: a result from an earlier request (e.g. before a file load)
        used to be shown as the answer to the next one."""
        st = ops.state()
        st.status, st.token, st.light_name, st.light_uid = "WAITING", "new-token", self.light.name, self.light.session_uid
        old = {"request_id": "a" * 24, "in_scope": True, "rationale": "OLD", "confidence": 0.9,
               "proposal": {"energy": 5.0}, "adjustments": []}
        ops.on_suggest_result(old, None, "old-token")
        self.assertEqual(st.status, "WAITING")
        self.assertEqual(len(st.rows), 0)
        ops.on_suggest_result(dict(old, rationale="NEW"), None, "new-token")
        self.assertEqual((st.status, st.rationale), ("READY", "NEW"))

    def test_poll_timer_survives_file_loads(self):
        jobs.run(lambda: None, lambda r, e: None)
        self.assertTrue(bpy.app.timers.is_registered(jobs.poll))
        jobs.wait_all(5)

    def test_cancel_while_waiting_and_late_result_ignored(self):
        """Review #5: WAITING could only be left by a result that might never come."""
        st = ops.state()
        st.status, st.token = "WAITING", "t1"
        self.assertTrue(ops.TLA_OT_discard.poll(bpy.context))
        bpy.ops.tla.discard()
        self.assertEqual(st.status, "IDLE")
        ops.on_suggest_result({"proposal": {"energy": 5.0}}, None, "t1")
        self.assertEqual(st.status, "IDLE")

    def test_reenabling_the_addon_clears_a_stuck_wait(self):
        bpy.context.window_manager.tla.status = "WAITING"
        bpy.ops.preferences.addon_disable(module=PKG)
        bpy.ops.preferences.addon_enable(module=PKG)
        _load_modules()  # re-enabling may re-import the package
        self.assertEqual(bpy.context.window_manager.tla.status, "IDLE")
        p = bpy.context.preferences.addons[PKG].preferences
        p.backend_url, p.api_key = BACKEND or "http://127.0.0.1:1", KEY or "tla_x"

    def test_callback_exception_becomes_an_error_not_a_hang(self):
        st = ops.state()
        st.status, st.token = "WAITING", "t2"
        ops.on_suggest_result({"proposal": "not a dict", "in_scope": True}, None, "t2")
        self.assertEqual(st.status, "ERROR")

    def test_apply_is_all_or_nothing(self):
        """Review #3: scalars were written before a preset step that could fail."""
        orig = lightstate._build_gobo
        lightstate._build_gobo = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(RuntimeError):
                lightstate.apply_values(self.light, {"energy": 400.0, "spot_blend": 0.5, "preset": "gobo_slot"})
        finally:
            lightstate._build_gobo = orig
        self.assertEqual(self.light.energy, 1000.0)
        self.assertAlmostEqual(self.light.spot_blend, 0.15, places=5)
        self.assertEqual(lightstate.preset_state(self.light), "none")
        self.assertNotIn(lightstate.PREV_NODES_KEY, self.light)

    def test_apply_operator_failure_leaves_light_unchanged(self):
        self.light.use_nodes = True
        nt = self.light.node_tree
        [nt.nodes.remove(n) for n in list(nt.nodes) if n.bl_idname == "ShaderNodeOutputLight"]
        nt.nodes.new("ShaderNodeTexNoise")  # tree with no output: not buildable, not ours
        st = self._ready({"energy": 400.0, "preset": "gobo_slot"})
        ops._report_outcome = lambda *a, **k: self.fail("no outcome for a failed apply")
        with self.assertRaises(RuntimeError):
            bpy.ops.tla.apply()
        self.assertEqual(self.light.energy, 1000.0)

    def test_emptied_node_tree_gets_default_nodes(self):
        self.light.use_nodes = True
        nt = self.light.node_tree
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        self.assertEqual(lightstate.preset_state(self.light), "none")
        lightstate.apply_values(self.light, {"energy": 400.0, "preset": "gobo_slot"})
        self.assertEqual(self.light.energy, 400.0)
        self.assertEqual(lightstate.preset_state(self.light), "gobo_slot")

    def test_url_check_blocks_cleartext_to_remote_hosts(self):
        """Review #7: prefix matching let the key go over http to other hosts."""
        for bad in ("http://localhost.evil.com", "http://127.0.0.1.nip.io", "http://127.0.0.1@evil.com",
                    "http://evil.com", "ftp://x", "https://user:pw@x.com", "", "https://", "http://127.0.0.1:99999"):
            with self.assertRaises(client.BackendError, msg=bad):
                client.check_url(bad)
        for good in ("https://abc.execute-api.us-west-2.amazonaws.com/demo", "http://127.0.0.1:8787", "http://localhost:8787/"):
            client.check_url(good)

    def test_client_timeout_covers_the_backend(self):
        """Review #11: a shorter client timeout gave up on calls it was charged for."""
        p = bpy.context.preferences.addons[PKG].preferences
        self.assertGreaterEqual(p.bl_rna.properties["timeout"].hard_min, 30)

    # --------------------------------------------------------------- snoots
    def _area(self):
        L = bpy.data.lights.new("Soft", "AREA")
        L.size, L.energy = 0.25, 400.0
        ob = bpy.data.objects.new("Soft", L)
        bpy.context.scene.collection.objects.link(ob)
        ob.scale = (1.5, 1.5, 1.5)
        return L, ob

    def _extent(self, light_obj, sn):
        """(back half-width, mouth half-width, length) in the light's local space."""
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = sn.evaluated_get(dg)
        M = light_obj.matrix_world.inverted() @ ev.matrix_world
        vs = [M @ v.co for v in sn.data.vertices]
        zmax, zmin = max(v.z for v in vs), min(v.z for v in vs)
        back = max(max(abs(v.x), abs(v.y)) for v in vs if abs(v.z - zmax) < 1e-4)
        front = max(max(abs(v.x), abs(v.y)) for v in vs if abs(v.z - zmin) < 1e-4)
        return back, front, zmax - zmin

    def test_spot_snoot_matches_the_hand_built_example(self):
        """Marian's spot example: back radius 1.08 x light radius, mouth half, length = back width."""
        self.light.shadow_soft_size = 0.5
        self.assertEqual(bpy.ops.tla.snoot_add(), {"FINISHED"})
        sn = snoot.find(self.ob)
        self.assertIsNotNone(sn)
        self.assertEqual(sn.parent, self.ob)
        self.assertEqual(len(sn.data.polygons), 32)  # open round tube
        back, front, length = self._extent(self.ob, sn)
        self.assertAlmostEqual(back, 0.54, places=3)
        self.assertAlmostEqual(front, 0.27, places=3)
        self.assertAlmostEqual(length, 1.08, places=3)
        self.assertEqual(sn.data.materials[0].name, snoot.MATERIAL_NAME)
        self.assertTrue(all(fc.driver.is_simple_expression for fc in sn.animation_data.drivers))

    def test_snoot_scales_with_the_light(self):
        self.light.shadow_soft_size = 0.5
        bpy.ops.tla.snoot_add()
        sn = snoot.find(self.ob)
        self.light.shadow_soft_size = 1.0
        back, front, length = self._extent(self.ob, sn)
        self.assertAlmostEqual(back, 1.08, places=3)
        self.assertAlmostEqual(length, 2.16, places=3)
        self.light.shadow_soft_size = 0.0  # a point light still gets a small snoot
        back, _, _ = self._extent(self.ob, sn)
        self.assertAlmostEqual(back, 0.054, places=3)

    def test_area_snoot_matches_the_hand_built_example(self):
        """Marian's area example: box, width = light size, mouth half, length 2.6 x width."""
        L, ob = self._area()
        bpy.context.view_layer.objects.active = ob
        bpy.ops.tla.snoot_add()
        sn = snoot.find(ob)
        self.assertEqual(len(sn.data.polygons), 4)  # open box
        back, front, length = self._extent(ob, sn)
        self.assertAlmostEqual(back, 0.126, places=3)
        self.assertAlmostEqual(front, 0.063, places=3)
        self.assertAlmostEqual(length, 0.655, places=2)
        L.size = 0.5
        back, _, _ = self._extent(ob, sn)
        self.assertAlmostEqual(back, 0.252, places=3)
        L.shape, L.size_y = "RECTANGLE", 1.0  # y follows size_y for rectangles
        self.assertTrue(snoot.needs_refit(ob))
        bpy.ops.tla.snoot_refit()
        dg = bpy.context.evaluated_depsgraph_get(); dg.update()
        self.assertAlmostEqual(snoot.find(ob).evaluated_get(dg).scale[1], 0.504, places=3)
        L.shape = "DISK"
        self.assertTrue(snoot.needs_refit(ob))
        bpy.ops.tla.snoot_refit()
        self.assertEqual(len(snoot.find(ob).data.polygons), 32)

    def test_ellipse_snoot_follows_both_axes(self):
        """Review: AreaLight.shape is SQUARE=0, RECTANGLE=1, DISK=4, ELLIPSE=5."""
        L, ob = self._area()
        L.shape, L.size, L.size_y = "ELLIPSE", 2.0, 0.5
        bpy.context.view_layer.objects.active = ob
        bpy.ops.tla.snoot_add()
        dg = bpy.context.evaluated_depsgraph_get(); dg.update()
        sc = snoot.find(ob).evaluated_get(dg).scale
        self.assertAlmostEqual(sc[0], 1.008, places=3)
        self.assertAlmostEqual(sc[1], 0.252, places=3)
        self.assertEqual(len(snoot.find(ob).data.polygons), 32)
        L.shape = "SQUARE"
        self.assertTrue(snoot.needs_refit(ob))

    def _child_mesh(self, name, back=0.6, front=0.3, depth=1.5, n=16):
        import bmesh as _bm
        me = bpy.data.meshes.new(name)
        bm = _bm.new()
        a = [bm.verts.new((back * math.cos(2 * math.pi * i / n), back * math.sin(2 * math.pi * i / n), 0)) for i in range(n)]
        b = [bm.verts.new((front * math.cos(2 * math.pi * i / n), front * math.sin(2 * math.pi * i / n), -depth)) for i in range(n)]
        for i in range(n):
            bm.faces.new((a[i], a[(i + 1) % n], b[(i + 1) % n], b[i]))
        bm.to_mesh(me); bm.free()
        o = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(o)
        o.parent = self.ob
        return o

    def test_unrelated_children_are_not_mistaken_for_snoots(self):
        """Review: 'Snoot bracket' used to be hidden by Convert and block the feature."""
        bracket = self._child_mesh("Snoot bracket")
        clamp = self._child_mesh("SnootClamp_bracket")
        self.assertIsNone(snoot.find_handmade(self.ob))
        self.assertTrue(ops.TLA_OT_snoot_add.poll(bpy.context))
        self.assertFalse(lightstate.read_state(self.light, self.ob).get("snoot_custom", False))
        bpy.ops.tla.snoot_add()
        self.assertFalse(bracket.hide_render or clamp.hide_render)

    def test_convert_refuses_ambiguity_and_touches_nothing(self):
        a = self._child_mesh("Spot.Snoot")
        b = self._child_mesh("Spot.Snoot.001")
        with self.assertRaises(ValueError):
            snoot.convert_handmade(self.ob)
        self.assertEqual((a.name, b.name, a.hide_render, b.hide_render), ("Spot.Snoot", "Spot.Snoot.001", False, False))
        self.assertIsNone(snoot.find(self.ob))

    def test_convert_then_restore_brings_the_original_back(self):
        hand = self._child_mesh("Key Snoot")
        bpy.ops.tla.snoot_convert()
        self.assertTrue(hand.hide_render)
        self.assertEqual(lightstate.read_state(self.light, self.ob)["snoot"], True)
        lightstate.apply_values(self.light, {"snoot": False}, self.ob)  # the assistant removes the managed one
        self.assertTrue(hand.hide_render)  # the original stays safely hidden...
        self.assertTrue(ops.TLA_OT_snoot_restore.poll(bpy.context))  # ...and can be restored
        bpy.ops.tla.snoot_restore()
        self.assertEqual((hand.name, hand.hide_render), ("Key Snoot", False))
        self.assertNotIn("tla_replaced", hand)
        self.assertIsNotNone(snoot.find_handmade(self.ob))

    def test_size_y_ignored_on_square_area_lights(self):
        L, ob = self._area()
        v = lightstate.apply_values(L, {"size_y": 2.0}, ob)
        self.assertNotIn("size_y", v)
        self.assertEqual(L.size_y, 0.25)

    def test_length_and_mouth_rebuild_live(self):
        bpy.ops.tla.snoot_add()
        sn = snoot.find(self.ob)
        sn.tla_snoot_mouth = 1.0  # UI slider -> update callback
        sn.tla_snoot_length = 2.0
        back, front, length = self._extent(self.ob, sn)
        self.assertAlmostEqual(front, back, places=4)
        self.assertAlmostEqual(length, 4 * back, places=3)

    def test_remove_snoot_and_undo_flags(self):
        for op in (ops.TLA_OT_snoot_add, ops.TLA_OT_snoot_remove, ops.TLA_OT_snoot_refit, ops.TLA_OT_snoot_convert):
            self.assertIn("UNDO", op.bl_options)
        bpy.ops.tla.snoot_add()
        self.assertFalse(ops.TLA_OT_snoot_add.poll(bpy.context))  # one snoot per light
        bpy.ops.tla.snoot_remove()
        self.assertIsNone(snoot.find(self.ob))
        self.assertFalse(any(m.name.endswith(".Snoot") for m in bpy.data.meshes))

    def test_convert_hand_built_snoot_keeps_its_proportions(self):
        """Like the hand-made ones in thornbury_demo.blend: a plain mesh child named *.Snoot."""
        self.light.shadow_soft_size = 0.5
        import bmesh as _bm
        me = bpy.data.meshes.new("Cylinder.001")
        bm = _bm.new()
        n, back, front, z1 = 16, 0.6, 0.3, -1.5
        a = [bm.verts.new((back * math.cos(2 * math.pi * i / n), back * math.sin(2 * math.pi * i / n), 0)) for i in range(n)]
        b = [bm.verts.new((front * math.cos(2 * math.pi * i / n), front * math.sin(2 * math.pi * i / n), z1)) for i in range(n)]
        for i in range(n):
            bm.faces.new((a[i], a[(i + 1) % n], b[(i + 1) % n], b[i]))
        bm.to_mesh(me); bm.free()
        hand = bpy.data.objects.new("Spot.Snoot", me)
        bpy.context.scene.collection.objects.link(hand)
        hand.parent = self.ob
        self.assertEqual(lightstate.read_state(self.light, self.ob).get("snoot_custom"), True)
        self.assertFalse(ops.TLA_OT_snoot_add.poll(bpy.context))
        with self.assertRaises(ValueError):
            lightstate.apply_values(self.light, {"snoot": True}, self.ob)
        self.assertEqual(bpy.ops.tla.snoot_convert(), {"FINISHED"})
        sn = snoot.find(self.ob)
        self.assertEqual(sn.name, self.ob.name + ".Snoot")
        self.assertAlmostEqual(sn.tla_snoot_length, 1.5 / 1.2, places=3)
        self.assertAlmostEqual(sn.tla_snoot_mouth, 0.5, places=3)
        self.assertTrue(hand.hide_render)  # hidden, not deleted
        self.assertIn(hand.name, bpy.data.objects)

    def test_assistant_can_add_adjust_and_remove_a_snoot(self):
        st = self._ready({"snoot": True, "snoot_mouth": 0.35})
        ops._report_outcome = lambda *a, **k: None
        self.assertEqual({r.field for r in st.rows}, {"snoot", "snoot_mouth"})
        bpy.ops.tla.apply()
        sn = snoot.find(self.ob)
        self.assertAlmostEqual(sn.tla_snoot_mouth, 0.35, places=4)
        self.assertEqual(lightstate.read_state(self.light, self.ob)["snoot"], True)
        lightstate.apply_values(self.light, {"snoot_length": 2.0}, self.ob)
        self.assertAlmostEqual(snoot.find(self.ob).tla_snoot_length, 2.0)
        lightstate.apply_values(self.light, {"snoot": False}, self.ob)
        self.assertIsNone(snoot.find(self.ob))

    def test_failed_apply_rolls_the_snoot_back_too(self):
        orig = lightstate._update_gobo_scale
        lightstate.apply_values(self.light, {"preset": "gobo_slot"}, self.ob)
        lightstate._update_gobo_scale = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(RuntimeError):
                lightstate.apply_values(self.light, {"energy": 5.0, "snoot": True}, self.ob)
        finally:
            lightstate._update_gobo_scale = orig
        self.assertIsNone(snoot.find(self.ob))
        self.assertEqual(self.light.energy, 1000.0)

    def test_area_light_state_and_apply(self):
        L, ob = self._area()
        s = lightstate.read_state(L, ob)
        self.assertEqual((s["type"], s["size"], s["shape"], s["snoot"]), ("AREA", 0.25, "SQUARE", False))
        self.assertNotIn("spot_size", s)
        v = lightstate.apply_values(L, {"size": 0.5, "spread": 0.0, "spot_size": 0.3, "preset": "gobo_slot", "energy": 200}, ob)
        self.assertEqual(L.size, 0.5)
        self.assertAlmostEqual(L.spread, math.radians(1.0), places=5)
        self.assertNotIn("spot_size", v)  # spot-only fields never touch an area light
        self.assertNotIn("preset", v)

    # -------------------------------------------------------------- gallery
    def test_every_preset_has_a_rendered_thumbnail(self):
        for pid in ["none"] + list(lightstate.LIBRARY):
            self.assertTrue(os.path.exists(os.path.join(gallery.THUMB_DIR, pid + ".png")), pid)
        gobos = gallery.gobo_items(None, bpy.context)
        self.assertEqual(len(gobos), 1 + sum(1 for p in lightstate.LIBRARY.values() if p["kind"] == "gobo"))
        self.assertGreaterEqual(len(gobos), 25)
        self.assertEqual(len(gallery.ies_items(None, bpy.context)), 4)
        self.assertEqual(len({i[4] for i in gobos}), len(gobos))  # unique enum numbers

    def test_pick_and_use_a_gobo_then_a_profile(self):
        self.assertIn("UNDO", gallery.TLA_OT_gobo_use.bl_options)
        wm = bpy.context.window_manager
        wm.tla_pick_gobo = "gobo_window_arched"
        self.assertEqual(bpy.ops.tla.gobo_use(kind="gobo"), {"FINISHED"})
        self.assertEqual(lightstate.preset_state(self.light), "gobo_window_arched")
        wm.tla_pick_ies = "ies_narrow_spot"
        bpy.ops.tla.gobo_use(kind="ies")
        self.assertEqual(lightstate.preset_state(self.light), "ies_narrow_spot")  # one pattern at a time
        wm.tla_pick_gobo = "none"
        bpy.ops.tla.gobo_use(kind="gobo")
        self.assertEqual(lightstate.preset_state(self.light), "none")

    def test_picker_refuses_custom_node_trees_and_area_lights(self):
        self.light.use_nodes = True
        self.light.node_tree.nodes.new("ShaderNodeTexNoise")
        self.assertFalse(gallery.TLA_OT_gobo_use.poll(bpy.context))
        L, ob = self._area()
        bpy.context.view_layer.objects.active = ob
        self.assertFalse(gallery.TLA_OT_gobo_use.poll(bpy.context))

    def test_rotate_size_offset_drive_the_mapping_node(self):
        lightstate.apply_values(self.light, {"preset": "gobo_blinds_wide"}, self.ob)
        xf = self.light.node_tree.nodes["TLA Gobo Transform"]
        self.light.tla_gobo_rotation = math.radians(90)
        self.light.tla_gobo_size = 2.0
        self.light.tla_gobo_offset = (0.1, -0.2)
        self.assertAlmostEqual(xf.inputs["Rotation"].default_value[2], -math.radians(90), places=5)
        self.assertAlmostEqual(xf.inputs["Scale"].default_value[0], 0.5, places=5)
        self.assertAlmostEqual(xf.inputs["Location"].default_value[1], 0.2, places=5)
        # Switching gobo keeps the artist's transform.
        lightstate.apply_values(self.light, {"preset": "gobo_bars"}, self.ob)
        xf = self.light.node_tree.nodes["TLA Gobo Transform"]
        self.assertAlmostEqual(xf.inputs["Scale"].default_value[0], 0.5, places=5)

    def test_gobos_from_older_versions_gain_the_transform(self):
        lightstate.apply_values(self.light, {"preset": "gobo_slot"}, self.ob)
        nt = self.light.node_tree
        for n in ("TLA Gobo Transform", "TLA Gobo Center"):  # what a 0.2 file looks like
            nt.nodes.remove(nt.nodes[n])
        nt.links.new(nt.nodes["TLA Gobo UV"].outputs[0], nt.nodes["TLA Gobo Image"].inputs[0])
        self.assertEqual(lightstate.preset_state(self.light), "gobo_slot")
        self.light.tla_gobo_rotation = 0.5
        self.assertIn("TLA Gobo Transform", nt.nodes)
        self.assertEqual(lightstate.preset_state(self.light), "gobo_slot")

    def _png(self, name, w=32):
        import tempfile
        img = bpy.data.images.new(name, w, w)
        path = os.path.join(tempfile.mkdtemp(), name + ".png")
        img.filepath_raw, img.file_format = path, "PNG"
        img.save()
        bpy.data.images.remove(img)
        return path

    def test_add_your_own_gobo_image(self):
        path = self._png("my_cookie")
        self.assertEqual(bpy.ops.tla.gobo_add_image(filepath=path), {"FINISHED"})
        pid = lightstate.preset_state(self.light)
        self.assertTrue(pid.startswith("user:"))
        user = lightstate.user_gobo_image(pid)
        self.assertIsNotNone(user.packed_file)
        self.assertIn(pid, [i[0] for i in gallery.gobo_items(None, bpy.context)])
        self.assertEqual(self.light.node_tree.nodes["TLA Gobo Image"].image, user)
        self.assertEqual(lightstate.read_state(self.light, self.ob)["preset"], "user")  # no filenames to the backend
        lightstate.apply_values(self.light, {"preset": "gobo_stars"}, self.ob)  # the assistant can swap it
        self.assertEqual(lightstate.preset_state(self.light), "gobo_stars")

    def test_renaming_or_deleting_a_user_gobo_never_locks_the_light(self):
        """Review: a renamed/deleted user image used to make the light 'custom' for good."""
        bpy.ops.tla.gobo_add_image(filepath=self._png("bbb"))
        pid = lightstate.preset_state(self.light)
        img = lightstate.user_gobo_image(pid)
        img.name = "renamed"
        self.assertEqual(lightstate.preset_state(self.light), pid)  # identity survives a rename
        bpy.data.images.remove(img)
        self.assertEqual(lightstate.preset_state(self.light), "broken")
        self.assertTrue(gallery.TLA_OT_gobo_use.poll(bpy.context))
        self.assertEqual(lightstate.read_state(self.light, self.ob)["preset"], "none")
        bpy.context.window_manager.tla_pick_gobo = "gobo_ring"
        bpy.ops.tla.gobo_use(kind="gobo")
        self.assertEqual(lightstate.preset_state(self.light), "gobo_ring")

    def test_picker_numbers_are_stable_and_stale_picks_are_refused(self):
        bpy.ops.tla.gobo_add_image(filepath=self._png("aaa"))
        bpy.ops.tla.gobo_add_image(filepath=self._png("bbb"))
        wm = bpy.context.window_manager
        b = lightstate.preset_state(self.light)
        wm.tla_pick_gobo = b
        a_img = next(i for i in bpy.data.images if i.name.startswith("aaa"))
        bpy.data.images.remove(a_img)
        self.assertEqual(wm.tla_pick_gobo, b)  # still the same image, not a neighbour
        bpy.data.images.remove(lightstate.user_gobo_image(b))
        self.assertEqual(bpy.ops.tla.gobo_use.poll(), True)
        with self.assertRaises(RuntimeError):  # refused with a message, no silent no-op
            bpy.ops.tla.gobo_use(kind="gobo")

    def test_add_your_own_never_takes_over_an_existing_image(self):
        path = self._png("wall_tex")
        existing = bpy.data.images.load(path)
        bpy.ops.tla.gobo_add_image(filepath=path)
        self.assertNotIn(lightstate.USER_TAG, existing)
        self.assertIsNone(existing.packed_file)
        self.assertIsNot(self.light.node_tree.nodes["TLA Gobo Image"].image, existing)

    def test_long_image_names_still_work_with_the_backend(self):
        bpy.ops.tla.gobo_add_image(filepath=self._png("x" * 60))
        state = lightstate.read_state(self.light, self.ob)
        self.assertEqual(state["preset"], "user")
        self.assertLessEqual(len(lightstate.preset_state(self.light)), 20)

    def test_gobo_transform_is_not_animatable(self):
        """Update callbacks don't run for F-curves, so keyframes would silently do nothing."""
        for name in ("tla_gobo_rotation", "tla_gobo_size", "tla_gobo_offset"):
            self.assertFalse(bpy.types.Light.bl_rna.properties[name].is_animatable, name)

    def test_upgrading_an_old_gobo_keeps_image_settings(self):
        lightstate.apply_values(self.light, {"preset": "gobo_slot"}, self.ob)
        nt = self.light.node_tree
        for n in ("TLA Gobo Transform", "TLA Gobo Center"):
            nt.nodes.remove(nt.nodes[n])
        nt.links.new(nt.nodes["TLA Gobo UV"].outputs[0], nt.nodes["TLA Gobo Image"].inputs[0])
        nt.nodes["TLA Gobo Image"].interpolation = "Closest"
        self.light.tla_gobo_size = 1.5
        self.assertIn("TLA Gobo Transform", nt.nodes)
        self.assertEqual(nt.nodes["TLA Gobo Image"].interpolation, "Closest")

    def test_snoots_and_gobos_need_no_key_url_or_internet(self):
        """Only the notes assistant needs the backend; everything else is local."""
        p = ops.prefs()
        p.backend_url, p.api_key = "", ""
        bpy.context.preferences.system.use_online_access = False
        try:
            self.assertEqual(bpy.ops.tla.snoot_add(), {"FINISHED"})
            bpy.context.window_manager.tla_pick_gobo = "gobo_leaf_breakup"
            self.assertEqual(bpy.ops.tla.gobo_use(kind="gobo"), {"FINISHED"})
            self.light.tla_gobo_rotation = 0.5
            bpy.context.window_manager.tla_pick_ies = "ies_wide_flood"
            self.assertEqual(bpy.ops.tla.gobo_use(kind="ies"), {"FINISHED"})
            self.assertEqual(bpy.ops.tla.snoot_remove(), {"FINISHED"})
            L, ob = self._area()
            bpy.context.view_layer.objects.active = ob
            self.assertEqual(bpy.ops.tla.snoot_add(), {"FINISHED"})
        finally:
            bpy.context.preferences.system.use_online_access = True

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
        self.assertIn("snoot", fields)  # "snoot" means the physical snoot
        self.assertNotIn("color", fields)  # "keep it warm"
        self.assertIsNone(snoot.find(self.ob))
        self.assertEqual(bpy.ops.tla.apply(), {"FINISHED"})
        self.assertIsNotNone(snoot.find(self.ob))
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
