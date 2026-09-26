# Trying the Thornbury Lighting Assistant (tester guide)

You need **Blender 4.2 or newer** (free, blender.org). `thornbury_demo.blend`
needs 4.5 or newer; the snoot showcase opens in 4.2. Snoots and gobos need
nothing else. For the notes assistant you also need the two things Marian sent
you: a **backend URL** and an **API key** (starts with `tla_`). Or host your own
backend with [docs/self-host.md](self-host.md).

## 1. Install (about 2 minutes)

1. Download `thornbury_lighting-0.3.2.zip`. Don't unzip it.
2. In Blender: **Edit > Preferences > Get Extensions** (or **Add-ons**), open the
   **⌄** menu at the top right, choose **Install from Disk…**, and pick the zip.
3. Still in Preferences, open **System > Network** and make sure
   **Allow Online Access** is ticked. The addon needs it to reach the backend.
4. Back under **Add-ons**, expand **Thornbury Lighting Assistant**, paste the
   **Backend URL** and **API key**, and click **Test Connection**. You should
   see "✓ Connected as … 0 of 50 used this month".

## 2. Try it

1. Open `thornbury_snoot_showcase.blend` (five lights, each with a different
   snoot) or `thornbury_demo.blend`, or any scene with a spot or area light,
   and select a light.
2. Find the panel either in **Properties > Light (green bulb tab) > Lighting
   Note Assistant**, or in the 3D view sidebar (**N**) under the **Thornbury** tab.
3. Type a note, e.g. *snoot the key down so it stops spilling on the
   background, keep it warm*, and click **Suggest**.
4. You'll see the current and proposed values side by side, a one-line
   reason and a confidence. **Nothing changes until you click Apply.** You
   can untick a row or edit a proposed value first. (While it's thinking,
   **Cancel** stops waiting.)
5. Click **Apply**, then render (F12) to see it. **Ctrl+Z** undoes the whole
   suggestion in one step. Or click **Discard**.

**Snoots.** The panel has a **Snoot** box. **Add Snoot** puts a physical snoot
(an open, tapered tube on spot lights, or a box on square area lights) on the
light. It follows the light and grows or shrinks with the light's Radius (spot)
or Size (area). Use **Length** and **Mouth** to shape it. If a light already has
a snoot you built by hand (a mesh child named "…Snoot"), **Convert Hand-Built
Snoot** replaces it with a managed one of the same proportions and hides the
original. You can also just ask: *snoot the key down*, *tighter snoot*, *lose
the snoot*.

**Pick a gobo by looking at it.** Select a spot light and open the **Gobo**
box in the panel. Click the big thumbnail to open a grid of 24 gobos (windows
and blinds, foliage and breakup, shapes and cuts, graphic patterns). Each
thumbnail is a real render of that gobo on a wall. Click one and it goes
straight onto the selected light (only that light). Then **Rotate**, **Size**
and **Offset** reshape it live. **Add your
own…** turns any image into a gobo (white lets light through, black blocks it,
colours tint it). The **Beam profile** grid below it works the same way for IES
profiles. No note and no internet needed; Ctrl+Z undoes each pick.

Good notes to try: "snoot it", "snoot it down more", "feather the edge", "half a stop down", "softer shadows",
"warm it up", "break it up like light through leaves", "cut it into a slot
like barn doors", "remove the gobo". It deliberately refuses placement or taste
notes ("move the key left", "make it more cinematic"): it only sets the
light's own controls.

**To see a gobo you need Cycles and Rendered view.** Gobos are textures on
the light, and only Cycles draws those: Material Preview, Solid and EEVEE
show nothing. If you're not there yet, the panel shows a **Show Gobos in the
Viewport** button that switches the engine to Cycles and the 3D view to
Rendered. If lights were made with Alt+D they share their settings, so a gobo
lands on all of them; the panel warns you and offers **Make This Light
Separate**.

## 3. What gets sent

Your note plus the selected light's cone, blend, power, colour, temperature,
radius, square setting and preset name. Nothing else from your scene, and no
images. Each suggestion is logged (note, values, what you did with it) so
Marian can see how the tool is used. You get 50 suggestions a month; the panel
shows how many you've used.

## 4. One thing to check for Marian

Please confirm **Ctrl+Z right after Apply puts the light back exactly** (cone,
power and any gobo). That's the one behaviour the automated tests can't cover,
because headless Blender has no undo.
