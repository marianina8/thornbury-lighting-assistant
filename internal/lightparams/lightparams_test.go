package lightparams

import (
	"math"
	"strings"
	"testing"
)

func fp(v float64) *float64 { return &v }
func bp(v bool) *bool       { return &v }
func sp(v string) *string   { return &v }

func base() Light {
	return Light{Name: "Key", Type: "SPOT", SpotSize: math.Pi / 4, SpotBlend: 0.15, Energy: 1000,
		Color: [3]float64{1, 0.8, 0.6}, ShadowSoftSize: 0.25, Preset: "none"}
}

func lib(id string) bool { return id == "gobo_window_blinds" || id == "ies_narrow_spot" }

func hasAdj(adj []Adjustment, field string) bool {
	for _, a := range adj {
		if a.Field == field {
			return true
		}
	}
	return false
}

func TestClampSpotSizeToBlenderRange(t *testing.T) {
	cur := base()
	for _, tc := range []struct{ in, want float64 }{
		{0, SpotSizeMin}, {-1, SpotSizeMin}, {10, SpotSizeMax}, {0.5, 0.5},
	} {
		out, adj := Clamp(cur, Capabilities{}, Proposal{SpotSize: fp(tc.in)}, lib)
		if out.SpotSize == nil || *out.SpotSize != tc.want {
			t.Fatalf("spot_size %v: got %v want %v", tc.in, out.SpotSize, tc.want)
		}
		if (tc.in != tc.want) != hasAdj(adj, "spot_size") {
			t.Fatalf("spot_size %v: adjustment reporting wrong: %+v", tc.in, adj)
		}
	}
}

func TestSpotSizeBoundsSitInsideBlenderFloat32Limits(t *testing.T) {
	// Values read from bpy RNA (float32 widened to float64).
	const rnaMin, rnaMax = 0.01745329238474369, 3.1415927410125732
	if SpotSizeMin < rnaMin || SpotSizeMax > rnaMax {
		t.Fatalf("our bounds [%v,%v] fall outside Blender's [%v,%v]", SpotSizeMin, SpotSizeMax, rnaMin, rnaMax)
	}
}

func TestClampBlend(t *testing.T) {
	out, adj := Clamp(base(), Capabilities{}, Proposal{SpotBlend: fp(1.7)}, lib)
	if *out.SpotBlend != 1 || !hasAdj(adj, "spot_blend") {
		t.Fatal("blend not clamped to 1")
	}
	out, _ = Clamp(base(), Capabilities{}, Proposal{SpotBlend: fp(-0.2)}, lib)
	if *out.SpotBlend != 0 {
		t.Fatal("blend not clamped to 0")
	}
}

func TestEnergyNeverNegativeAndStepLimited(t *testing.T) {
	cur := base() // 1000 W
	out, adj := Clamp(cur, Capabilities{}, Proposal{Energy: fp(-50)}, lib)
	if *out.Energy != 250 || !hasAdj(adj, "energy") {
		t.Fatalf("negative energy: got %v", *out.Energy)
	}
	out, _ = Clamp(cur, Capabilities{}, Proposal{Energy: fp(9000)}, lib)
	if *out.Energy != 4000 {
		t.Fatalf("energy step cap: got %v", *out.Energy)
	}
	out, adj = Clamp(cur, Capabilities{}, Proposal{Energy: fp(600)}, lib)
	if *out.Energy != 600 || len(adj) != 0 {
		t.Fatalf("in-range energy changed: %v %+v", *out.Energy, adj)
	}
	// Light currently off: no step rule, but still never negative / above the slider max.
	cur.Energy = 0
	out, _ = Clamp(cur, Capabilities{}, Proposal{Energy: fp(-3)}, lib)
	if out.Energy != nil {
		t.Fatalf("clamping -3 to 0 on a 0 W light should be a dropped no-op, got %v", *out.Energy)
	}
	out, _ = Clamp(cur, Capabilities{}, Proposal{Energy: fp(5e7)}, lib)
	if *out.Energy != EnergyFromOffMax {
		t.Fatalf("turning an off light on is capped at %v, got %v", EnergyFromOffMax, *out.Energy)
	}
}

// Regression (review #6): the step window used to be skipped for lights at or
// below 0 W, and inverted above 4 MW (lo > hi), letting a value exceed EnergyMax.
func TestEnergyEdges(t *testing.T) {
	cur := base()
	cur.Energy = -50
	out, _ := Clamp(cur, Capabilities{}, Proposal{Energy: fp(1e6)}, lib)
	if *out.Energy != EnergyFromOffMax {
		t.Fatalf("negative light: %v", *out.Energy)
	}
	cur.Energy = 5e6 // above the policy max (Blender allows it)
	for _, req := range []float64{1e9, 2e6, 1} {
		out, adj := Clamp(cur, Capabilities{}, Proposal{Energy: fp(req)}, lib)
		if *out.Energy > EnergyMax || *out.Energy < 0 {
			t.Fatalf("req %v on a 5 MW light gave %v", req, *out.Energy)
		}
		for _, a := range adj {
			if strings.Contains(a.Reason, "1.25e+06–1e+06") {
				t.Fatalf("inverted range in message: %s", a.Reason)
			}
		}
	}
}

func TestNonFiniteValuesAreDropped(t *testing.T) {
	out, adj := Clamp(base(), Capabilities{}, Proposal{SpotSize: fp(math.NaN()), Energy: fp(math.Inf(1)),
		Color: &[3]float64{1, math.NaN(), 0}}, lib)
	if out.SpotSize != nil || out.Energy != nil || out.Color != nil {
		t.Fatalf("non-finite values leaked: %+v", out)
	}
	if len(adj) != 3 {
		t.Fatalf("want 3 adjustments, got %+v", adj)
	}
}

func TestColorClampedPerChannel(t *testing.T) {
	out, adj := Clamp(base(), Capabilities{}, Proposal{Color: &[3]float64{1.4, 0.5, -0.1}}, lib)
	if *out.Color != [3]float64{1, 0.5, 0} || !hasAdj(adj, "color") {
		t.Fatalf("color: %+v", out.Color)
	}
}

func TestTemperatureOnSupportedBlenderEnablesIt(t *testing.T) {
	out, adj := Clamp(base(), Capabilities{Temperature: true}, Proposal{Temperature: fp(500)}, lib)
	if *out.Temperature != TemperatureMin || out.UseTemperature == nil || !*out.UseTemperature {
		t.Fatalf("temperature: %+v", out)
	}
	if !hasAdj(adj, "temperature") {
		t.Fatal("temperature clamp not reported")
	}
}

func TestTemperatureOnOldBlenderBecomesRGB(t *testing.T) {
	out, adj := Clamp(base(), Capabilities{Temperature: false}, Proposal{Temperature: fp(3200)}, lib)
	if out.Temperature != nil || out.UseTemperature != nil {
		t.Fatal("temperature must not be sent to a Blender without the property")
	}
	if out.Color == nil || !(out.Color[0] == 1 && out.Color[2] < out.Color[1]) {
		t.Fatalf("3200 K should become a warm RGB, got %+v", out.Color)
	}
	if !hasAdj(adj, "temperature") {
		t.Fatal("conversion not reported")
	}
	// If a colour was also proposed, the colour wins and the temperature is dropped.
	out, _ = Clamp(base(), Capabilities{}, Proposal{Temperature: fp(3200), Color: &[3]float64{0.2, 0.3, 1}}, lib)
	if *out.Color != [3]float64{0.2, 0.3, 1} {
		t.Fatal("explicit colour should win")
	}
}

func TestKelvinToRGB(t *testing.T) {
	warm, daylight, cool := KelvinToRGB(2700), KelvinToRGB(6600), KelvinToRGB(12000)
	if !(warm[0] == 1 && warm[2] < 0.3) {
		t.Fatalf("2700 K should be orange: %v", warm)
	}
	for _, c := range daylight {
		if c < 0.9 {
			t.Fatalf("6600 K should be near white: %v", daylight)
		}
	}
	if !(cool[2] == 1 && cool[0] < 1) {
		t.Fatalf("12000 K should be blue: %v", cool)
	}
}

func TestPresetValidation(t *testing.T) {
	cur := base()
	out, adj := Clamp(cur, Capabilities{}, Proposal{Preset: sp("gobo_made_up")}, lib)
	if out.Preset != nil || !hasAdj(adj, "preset") {
		t.Fatal("unknown preset must be dropped")
	}
	out, _ = Clamp(cur, Capabilities{}, Proposal{Preset: sp("gobo_window_blinds")}, lib)
	if *out.Preset != "gobo_window_blinds" {
		t.Fatal("known preset dropped")
	}
	cur.Preset = "gobo_window_blinds"
	out, _ = Clamp(cur, Capabilities{}, Proposal{Preset: sp("none")}, lib)
	if *out.Preset != "none" {
		t.Fatal("removing a preset should be allowed")
	}
	cur.Preset = "custom"
	out, adj = Clamp(cur, Capabilities{}, Proposal{Preset: sp("ies_narrow_spot")}, lib)
	if out.Preset != nil || !strings.Contains(adj[0].Reason, "hand-built") {
		t.Fatal("must never propose a preset over a custom node tree")
	}
}

func TestNoOpsDropped(t *testing.T) {
	cur := base()
	out, _ := Clamp(cur, Capabilities{}, Proposal{SpotSize: fp(cur.SpotSize), Energy: fp(cur.Energy),
		Color: &cur.Color, UseSquare: bp(false), Preset: sp("none")}, lib)
	if !out.Empty() {
		t.Fatalf("no-op proposal should be empty: %+v", out)
	}
}

func TestValidateCurrent(t *testing.T) {
	good := base()
	if err := ValidateCurrent(good); err != nil {
		t.Fatal(err)
	}
	bad := []func(*Light){
		func(l *Light) { l.Type = "POINT" },
		func(l *Light) { l.Name = "" },
		func(l *Light) { l.Energy = math.NaN() },
		func(l *Light) { l.SpotSize = 4 },
		func(l *Light) { l.SpotBlend = 2 },
		func(l *Light) { l.ShadowSoftSize = -1 },
		func(l *Light) { l.Color[1] = math.Inf(-1) },
	}
	for i, mut := range bad {
		l := base()
		mut(&l)
		if ValidateCurrent(l) == nil {
			t.Fatalf("case %d should fail", i)
		}
	}
	// Blender's float32 bounds round-trip through JSON a hair outside float64 bounds.
	l := base()
	l.SpotSize = 3.1415927410125732
	if err := ValidateCurrent(l); err != nil {
		t.Fatalf("float32 max should be accepted: %v", err)
	}
}

func area() Light {
	return Light{Name: "Soft", Type: "AREA", Energy: 400, Color: [3]float64{1, 1, 1}, Size: 0.25, SizeY: 0.25,
		Shape: "SQUARE", Spread: math.Pi, Preset: "none"}
}

func TestAreaLightValidates(t *testing.T) {
	if err := ValidateCurrent(area()); err != nil {
		t.Fatal(err)
	}
	bad := area()
	bad.Shape = "TRIANGLE"
	if ValidateCurrent(bad) == nil {
		t.Fatal("unknown shape accepted")
	}
	bad = area()
	bad.Spread = 4
	if ValidateCurrent(bad) == nil {
		t.Fatal("spread > 180° accepted")
	}
	bad = area()
	bad.Type = "SUN"
	if ValidateCurrent(bad) == nil {
		t.Fatal("sun accepted")
	}
}

func TestSettingsNeverCrossLightTypes(t *testing.T) {
	out, adj := Clamp(area(), Capabilities{}, Proposal{SpotSize: fp(0.3), SpotBlend: fp(0.2), UseSquare: bp(true),
		ShadowSoftSize: fp(1), Preset: sp("gobo_window_blinds"), Size: fp(0.5), Spread: fp(0)}, lib)
	if out.SpotSize != nil || out.SpotBlend != nil || out.UseSquare != nil || out.ShadowSoftSize != nil || out.Preset != nil {
		t.Fatalf("spot settings leaked onto an area light: %+v", out)
	}
	if *out.Size != 0.5 || *out.Spread != SpreadMin {
		t.Fatalf("area settings: %+v", out)
	}
	if len(adj) != 6 {
		t.Fatalf("want 6 adjustments, got %d: %+v", len(adj), adj)
	}
	out, adj = Clamp(base(), Capabilities{}, Proposal{Size: fp(2), SizeY: fp(2), Spread: fp(1)}, lib)
	if !out.Empty() || len(adj) != 3 {
		t.Fatalf("area settings leaked onto a spot light: %+v %+v", out, adj)
	}
}

func TestSnootProposals(t *testing.T) {
	// Add with shape.
	out, _ := Clamp(base(), Capabilities{}, Proposal{Snoot: bp(true), SnootMouth: fp(0.05), SnootLength: fp(99)}, lib)
	if !*out.Snoot || *out.SnootMouth != SnootMouthMin || *out.SnootLength != SnootLengthMax {
		t.Fatalf("add: %+v", out)
	}
	// Shaping a snoot that isn't there implies adding one.
	out, _ = Clamp(area(), Capabilities{}, Proposal{SnootMouth: fp(0.4)}, lib)
	if out.Snoot == nil || !*out.Snoot {
		t.Fatalf("implied add: %+v", out)
	}
	// Removing ignores shape fields.
	cur := base()
	cur.Snoot, cur.SnootLength, cur.SnootMouth = true, 1, 0.5
	out, adj := Clamp(cur, Capabilities{}, Proposal{Snoot: bp(false), SnootMouth: fp(0.3)}, lib)
	if *out.Snoot || out.SnootMouth != nil || len(adj) != 1 {
		t.Fatalf("remove: %+v %+v", out, adj)
	}
	// No-ops dropped.
	out, _ = Clamp(cur, Capabilities{}, Proposal{Snoot: bp(true), SnootLength: fp(1), SnootMouth: fp(0.5)}, lib)
	if !out.Empty() {
		t.Fatalf("no-op snoot: %+v", out)
	}
	// A hand-built snoot is never touched.
	cur.SnootCustom = true
	out, adj = Clamp(cur, Capabilities{}, Proposal{Snoot: bp(false)}, lib)
	if out.Snoot != nil || !strings.Contains(adj[0].Reason, "hand-built") {
		t.Fatalf("custom snoot modified: %+v %+v", out, adj)
	}
}

func TestSizeYOnlyForRectangleAndEllipse(t *testing.T) {
	out, adj := Clamp(area(), Capabilities{}, Proposal{SizeY: fp(1)}, lib) // SQUARE
	if out.SizeY != nil || len(adj) != 1 {
		t.Fatalf("square: %+v %+v", out, adj)
	}
	cur := area()
	cur.Shape = "ELLIPSE"
	out, _ = Clamp(cur, Capabilities{}, Proposal{SizeY: fp(1)}, lib)
	if out.SizeY == nil || *out.SizeY != 1 {
		t.Fatalf("ellipse: %+v", out)
	}
}

func TestUserGoboIsReplaceableNotCustom(t *testing.T) {
	cur := base()
	cur.Preset = "user"
	if err := ValidateCurrent(cur); err != nil {
		t.Fatal(err)
	}
	out, _ := Clamp(cur, Capabilities{}, Proposal{Preset: sp("gobo_window_blinds")}, lib)
	if out.Preset == nil || *out.Preset != "gobo_window_blinds" {
		t.Fatalf("user gobo should be replaceable: %+v", out)
	}
	out, _ = Clamp(cur, Capabilities{}, Proposal{Preset: sp("user")}, lib)
	if out.Preset != nil {
		t.Fatal("the model can't invent a user gobo")
	}
}
