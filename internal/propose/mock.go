package propose

import (
	"context"
	"math"
	"strings"
)

// Mock is a deterministic, keyword-driven stand-in for the model, used by
// tests and by `cmd/local -model mock` so the whole loop (addon → backend →
// audit) can run without AWS. It is deliberately simple and says so in its
// rationale; it is never deployed.
type Mock struct {
	// Raw, if set, is returned verbatim (lets tests inject hostile output).
	Raw *Output
	Err error
}

func (m *Mock) ID() string { return "mock" }

func (m *Mock) Propose(_ context.Context, in Input) (Output, Usage, error) {
	if m.Err != nil {
		return Output{}, Usage{}, m.Err
	}
	if m.Raw != nil {
		return *m.Raw, Usage{InputTokens: 1, OutputTokens: 1}, nil
	}
	n := strings.ToLower(in.Note)
	has := func(words ...string) bool {
		for _, w := range words {
			if strings.Contains(n, w) {
				return true
			}
		}
		return false
	}
	t, f := true, false
	out := Output{InScope: &t}
	conf := 0.8
	var did []string
	cone := in.Light.SpotSize * 180 / math.Pi

	if has("move ", "aim ", "rotate", "reframe", "camera", "look better", "more cinematic") && !has("snoot", "spill", "tight", "narrow", "soft", "warm", "cool", "bright", "dim", "stop") {
		out.InScope = &f
		out.OutOfScopeReason = "That's placement or shot judgement, not this light's settings."
		out.Rationale = "Nothing changed; the note is about placement or taste."
		out.Confidence = &conf
		return out, Usage{InputTokens: 900, OutputTokens: 60}, nil
	}
	if has("snoot", "tighten", "narrow", "spill", "tighter") {
		v := math.Max(1, cone*0.6)
		out.ConeAngleDeg = &v
		did = append(did, "narrowed the cone")
	}
	if has("wider", "open up", "widen") {
		v := math.Min(180, cone*1.4)
		out.ConeAngleDeg = &v
		did = append(did, "widened the cone")
	}
	if has("feather", "soften the edge", "soft edge", "softer edge") {
		v := math.Min(1, in.Light.SpotBlend+0.25)
		out.Blend = &v
		did = append(did, "feathered the edge")
	}
	if has("hard edge", "harder edge", "crisp") {
		v := math.Max(0, in.Light.SpotBlend-0.1)
		out.Blend = &v
		did = append(did, "hardened the edge")
	}
	if has("softer shadow", "soft shadow") {
		v := math.Max(0.05, in.Light.ShadowSoftSize*2)
		out.RadiusM = &v
		did = append(did, "softened the shadows")
	}
	if has("brighter", "up a stop", "stop up", "more light") {
		v := in.Light.Energy * 2
		out.PowerW = &v
		did = append(did, "one stop brighter")
	}
	if has("dimmer", "down a stop", "stop down", "less light", "too hot") {
		v := in.Light.Energy / 2
		out.PowerW = &v
		did = append(did, "one stop down")
	}
	if has("warmer", "warm it up") && !has("keep it warm", "keep the warm") {
		k := 3200.0
		out.ColorTemperatureK = &k
		did = append(did, "warmed to 3200 K")
	}
	if has("cooler", "cool it", "moonlight") {
		k := 8000.0
		out.ColorTemperatureK = &k
		did = append(did, "cooled to 8000 K")
	}
	if has("blinds") {
		p := "gobo_window_blinds"
		out.Preset = &p
		did = append(did, "added the blinds gobo")
	} else if has("foliage", "leaves", "dappled", "break it up", "breakup") {
		p := "gobo_leaf_breakup"
		out.Preset = &p
		did = append(did, "added leaf breakup")
	} else if has("window") {
		p := "gobo_window_panes"
		out.Preset = &p
		did = append(did, "added the window gobo")
	} else if has("remove the gobo", "no gobo", "clear the gobo") {
		p := "none"
		out.Preset = &p
		did = append(did, "removed the gobo")
	}
	if len(did) == 0 {
		low := 0.3
		out.Rationale = "Mock model: no rule matched this note, so nothing changes."
		out.Confidence = &low
	} else {
		out.Rationale = "Mock model: " + strings.Join(did, ", ") + "."
		out.Confidence = &conf
	}
	return out, Usage{InputTokens: 900, OutputTokens: 80}, nil
}
