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
	if *out.Energy != EnergyMax {
		t.Fatalf("energy max: got %v", *out.Energy)
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
