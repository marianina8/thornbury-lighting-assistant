// Package lightparams is the contract between the backend and Blender's
// SpotLight and AreaLight data-blocks (plus the addon's physical snoot). Every value the model proposes passes through Clamp
// before it can reach the addon, so an out-of-range or nonsensical value is
// corrected (and reported) in plain code, never trusted.
//
// Ranges were read from the live RNA definitions (bpy.types.SpotLight/AreaLight.bl_rna)
// of Blender 4.2.0, 4.5.14 LTS, 5.0.1, 5.1.2 and 5.2.2 LTS on 2026-09-24.
// They were identical in every version; see docs/blender-api-verification.md.
package lightparams

import (
	"fmt"
	"math"
	"strings"
)

// Blender's hard limits (from RNA). Blender stores these as float32; the
// float64 values below sit inside the float32 limits so Blender never clamps
// a value we have already clamped.
const (
	SpotSizeMin = math.Pi / 180 // 1 degree; RNA hard_min 0.0174532924 (float32)
	SpotSizeMax = math.Pi       // 180 degrees; RNA hard_max 3.14159274 (float32)
	SpotBlendMin = 0.0
	SpotBlendMax = 1.0
	TemperatureMin = 800.0   // Kelvin, RNA hard range (Blender 4.5+)
	TemperatureMax = 20000.0 // Kelvin
	ShadowSoftSizeMin = 0.0  // metres, RNA hard_min
)

// Policy limits: tighter than Blender's own, chosen by us, and documented.
// Blender's RNA allows negative energy and unbounded colour/radius values; a
// lighting-note assistant never needs those.
const (
	EnergyMin = 0.0       // Blender allows negative energy (hard_min -FLT_MAX); we never propose it.
	EnergyMax = 1_000_000 // Blender's soft_max (the UI slider limit), in watts.
	// A single suggestion may change power by at most two stops either way.
	// Bigger moves are still possible, one reviewed step at a time.
	EnergyMaxStepRatio = 4.0
	// A light that is currently off (0 W or negative) has no reference for
	// the step rule; a suggestion may turn it on to at most this.
	EnergyFromOffMax = 1000.0
	ColorChannelMin    = 0.0 // RNA hard_min
	ColorChannelMax    = 1.0 // RNA soft_max; values above 1 are legal in Blender but never proposed.
	ShadowSoftSizeMax  = 100.0 // RNA soft_max (metres)

	// Area lights: RNA size/size_y are 0 – FLT_MAX (soft 100); spread 0 – 180°.
	AreaSizeMin = 0.01 // a 0 m area light is degenerate
	AreaSizeMax = 100.0
	SpreadMin   = math.Pi / 180 // 1°
	SpreadMax   = math.Pi

	// Snoot proportions, relative to its back opening (the addon's snoot.py).
	SnootLengthMin, SnootLengthMax = 0.25, 6.0
	SnootMouthMin, SnootMouthMax   = 0.2, 1.0
)

// AreaShapes are Blender's AreaLight.shape values.
var AreaShapes = map[string]bool{"SQUARE": true, "RECTANGLE": true, "DISK": true, "ELLIPSE": true}

// Light is the state of one spot or area light as the addon reads it from bpy.
// Spot-only fields are zero for area lights and vice versa.
// Temperature fields are only meaningful when Capabilities.Temperature is true
// (Blender 4.5+).
type Light struct {
	Name           string     `json:"name"`
	Type           string     `json:"type"`
	SpotSize       float64    `json:"spot_size"`
	SpotBlend      float64    `json:"spot_blend"`
	Energy         float64    `json:"energy"`
	Color          [3]float64 `json:"color"`
	ShadowSoftSize float64    `json:"shadow_soft_size"`
	UseSquare      bool       `json:"use_square"`
	UseTemperature bool       `json:"use_temperature,omitempty"`
	Temperature    float64    `json:"temperature,omitempty"`
	// Preset is the id of the addon-managed gobo/IES preset on the light,
	// "none" when the light has the default node tree (or no nodes), or
	// "custom" when the artist built their own node tree (which the addon
	// never modifies).
	Preset string `json:"preset"`

	// Area lights.
	Size   float64 `json:"size,omitempty"`
	SizeY  float64 `json:"size_y,omitempty"`
	Shape  string  `json:"shape,omitempty"`
	Spread float64 `json:"spread,omitempty"`

	// The physical snoot (a child mesh the addon manages). SnootCustom means
	// the artist built one by hand, which the assistant never modifies.
	Snoot       bool    `json:"snoot"`
	SnootLength float64 `json:"snoot_length,omitempty"`
	SnootMouth  float64 `json:"snoot_mouth,omitempty"`
	SnootCustom bool    `json:"snoot_custom,omitempty"`
}

// IsArea reports whether this is an area light.
func (l Light) IsArea() bool { return l.Type == "AREA" }

// Capabilities describes what the artist's Blender build supports.
type Capabilities struct {
	Temperature bool `json:"temperature"` // Light.temperature / use_temperature (4.5+)
}

// Proposal is a set of absolute new values. A nil field means "leave as is".
type Proposal struct {
	SpotSize       *float64    `json:"spot_size,omitempty"`
	SpotBlend      *float64    `json:"spot_blend,omitempty"`
	Energy         *float64    `json:"energy,omitempty"`
	Color          *[3]float64 `json:"color,omitempty"`
	ShadowSoftSize *float64    `json:"shadow_soft_size,omitempty"`
	UseSquare      *bool       `json:"use_square,omitempty"`
	UseTemperature *bool       `json:"use_temperature,omitempty"`
	Temperature    *float64    `json:"temperature,omitempty"`
	Preset         *string     `json:"preset,omitempty"`
	Size           *float64    `json:"size,omitempty"`
	SizeY          *float64    `json:"size_y,omitempty"`
	Spread         *float64    `json:"spread,omitempty"`
	Snoot          *bool       `json:"snoot,omitempty"`
	SnootLength    *float64    `json:"snoot_length,omitempty"`
	SnootMouth     *float64    `json:"snoot_mouth,omitempty"`
}

// Empty reports whether the proposal changes nothing.
func (p Proposal) Empty() bool {
	return p.SpotSize == nil && p.SpotBlend == nil && p.Energy == nil && p.Color == nil &&
		p.ShadowSoftSize == nil && p.UseSquare == nil && p.UseTemperature == nil &&
		p.Temperature == nil && p.Preset == nil && p.Size == nil && p.SizeY == nil && p.Spread == nil &&
		p.Snoot == nil && p.SnootLength == nil && p.SnootMouth == nil
}

// Adjustment records a value the code changed or dropped, so the artist sees
// exactly where the model was overruled.
type Adjustment struct {
	Field     string `json:"field"`
	Requested string `json:"requested"`
	Result    string `json:"result"`
	Reason    string `json:"reason"`
}

// PresetValidator reports whether id names a preset in the bundled library.
type PresetValidator func(id string) bool

// ValidateCurrent checks the state the addon sent. It rejects rather than
// clamps: bad input here means a broken or tampered client.
func ValidateCurrent(l Light) error {
	if l.Type != "SPOT" && l.Type != "AREA" {
		return fmt.Errorf("only spot and area lights are supported (got %q)", l.Type)
	}
	if len(l.Name) == 0 || len(l.Name) > 128 {
		return fmt.Errorf("light name must be 1-128 characters")
	}
	nums := map[string]float64{
		"spot_size": l.SpotSize, "spot_blend": l.SpotBlend, "energy": l.Energy,
		"shadow_soft_size": l.ShadowSoftSize, "temperature": l.Temperature,
		"color.r": l.Color[0], "color.g": l.Color[1], "color.b": l.Color[2],
		"size": l.Size, "size_y": l.SizeY, "spread": l.Spread,
		"snoot_length": l.SnootLength, "snoot_mouth": l.SnootMouth,
	}
	for k, v := range nums {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return fmt.Errorf("%s is not a finite number", k)
		}
	}
	// Allow float32 rounding slack on the values Blender itself enforces.
	const eps = 1e-6
	if l.IsArea() {
		if l.Size < -eps || l.SizeY < -eps {
			return fmt.Errorf("area size is negative")
		}
		if l.Spread < -eps || l.Spread > SpreadMax+eps {
			return fmt.Errorf("spread %.6f outside Blender's range", l.Spread)
		}
		if !AreaShapes[l.Shape] {
			return fmt.Errorf("unknown area shape %q", l.Shape)
		}
	} else {
		if l.SpotSize < SpotSizeMin-eps || l.SpotSize > SpotSizeMax+eps {
			return fmt.Errorf("spot_size %.6f outside Blender's range", l.SpotSize)
		}
		if l.SpotBlend < -eps || l.SpotBlend > 1+eps {
			return fmt.Errorf("spot_blend %.6f outside Blender's range", l.SpotBlend)
		}
		if l.ShadowSoftSize < -eps {
			return fmt.Errorf("shadow_soft_size is negative")
		}
	}
	if len(l.Preset) > 64 {
		return fmt.Errorf("preset id too long")
	}
	return nil
}

func f(v float64) string { return fmt.Sprintf("%.4g", v) }

func clampF(v, lo, hi float64) float64 { return math.Max(lo, math.Min(hi, v)) }

func finite(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }

// Clamp validates a raw proposal against Blender's ranges and our policy
// limits, returning the safe proposal plus a list of every correction.
// It also drops no-op changes (values equal to the current ones).
func Clamp(cur Light, caps Capabilities, in Proposal, validPreset PresetValidator) (Proposal, []Adjustment) {
	var out Proposal
	var adj []Adjustment
	note := func(field, req, res, why string) {
		adj = append(adj, Adjustment{Field: field, Requested: req, Result: res, Reason: why})
	}
	num := func(field string, p *float64, lo, hi float64, why string) *float64 {
		if p == nil {
			return nil
		}
		v := *p
		if !finite(v) {
			note(field, fmt.Sprint(v), "dropped", "not a finite number")
			return nil
		}
		c := clampF(v, lo, hi)
		if c != v {
			note(field, f(v), f(c), why)
		}
		return &c
	}

	// Settings that don't exist on this light type are dropped, never guessed.
	notHere := func(field string, set bool) bool {
		if set {
			kind := "a spot"
			if cur.IsArea() {
				kind = "an area"
			}
			note(field, "set", "dropped", "not a setting of "+kind+" light")
		}
		return set
	}
	if cur.IsArea() {
		if notHere("spot_size", in.SpotSize != nil) {
			in.SpotSize = nil
		}
		if notHere("spot_blend", in.SpotBlend != nil) {
			in.SpotBlend = nil
		}
		if notHere("shadow_soft_size", in.ShadowSoftSize != nil) {
			in.ShadowSoftSize = nil
		}
		if notHere("use_square", in.UseSquare != nil) {
			in.UseSquare = nil
		}
		if in.Preset != nil {
			note("preset", *in.Preset, "dropped", "gobo/IES presets are for spot lights")
			in.Preset = nil
		}
	} else {
		if notHere("size", in.Size != nil) {
			in.Size = nil
		}
		if notHere("size_y", in.SizeY != nil) {
			in.SizeY = nil
		}
		if notHere("spread", in.Spread != nil) {
			in.Spread = nil
		}
	}
	if cur.IsArea() && in.SizeY != nil && cur.Shape != "RECTANGLE" && cur.Shape != "ELLIPSE" {
		note("size_y", "set", "dropped", "square and disk area lights use size only")
		in.SizeY = nil
	}
	out.Size = num("size", in.Size, AreaSizeMin, AreaSizeMax, "area size limited to 0.01–100 m")
	out.SizeY = num("size_y", in.SizeY, AreaSizeMin, AreaSizeMax, "area size limited to 0.01–100 m")
	out.Spread = num("spread", in.Spread, SpreadMin, SpreadMax, "spread limited to 1°–180°")

	// Snoot: the addon's physical snoot. A hand-built one is never modified.
	if in.Snoot != nil || in.SnootLength != nil || in.SnootMouth != nil {
		if cur.SnootCustom {
			note("snoot", "change", "dropped", "this light has a hand-built snoot; convert it in the panel first")
		} else if in.Snoot != nil && !*in.Snoot {
			out.Snoot = in.Snoot
			if in.SnootLength != nil || in.SnootMouth != nil {
				note("snoot_length/mouth", "set", "dropped", "the snoot is being removed")
			}
		} else {
			out.Snoot = in.Snoot
			out.SnootLength = num("snoot_length", in.SnootLength, SnootLengthMin, SnootLengthMax, "snoot length limited to 0.25–6× its opening")
			out.SnootMouth = num("snoot_mouth", in.SnootMouth, SnootMouthMin, SnootMouthMax, "snoot mouth limited to 0.2–1× its opening")
			if out.Snoot == nil && !cur.Snoot && (out.SnootLength != nil || out.SnootMouth != nil) {
				t := true
				out.Snoot = &t // shaping a snoot that isn't there means adding one
			}
		}
	}

	out.SpotSize = num("spot_size", in.SpotSize, SpotSizeMin, SpotSizeMax, "Blender's spot size range is 1°–180°")
	out.SpotBlend = num("spot_blend", in.SpotBlend, SpotBlendMin, SpotBlendMax, "Blender's blend range is 0–1")
	out.ShadowSoftSize = num("shadow_soft_size", in.ShadowSoftSize, ShadowSoftSizeMin, ShadowSoftSizeMax, "radius limited to 0–100 m")

	if in.Energy != nil {
		lo, hi := EnergyMin, float64(EnergyMax)
		var why string
		if cur.Energy > 0 {
			lo = math.Max(lo, cur.Energy/EnergyMaxStepRatio)
			hi = math.Min(hi, cur.Energy*EnergyMaxStepRatio)
			lo = math.Min(lo, hi) // a light above the policy max can still only go down to it
			why = fmt.Sprintf("power limited to 2 stops per suggestion and 0–1,000,000 W (%s–%s W)", f(lo), f(hi))
		} else {
			hi = EnergyFromOffMax
			why = "the light is off, so a suggestion may turn it on to at most 1000 W"
		}
		out.Energy = num("energy", in.Energy, lo, hi, why)
	}

	if in.Color != nil {
		c := *in.Color
		ok := true
		for _, ch := range c {
			if !finite(ch) {
				ok = false
			}
		}
		if !ok {
			note("color", "non-finite", "dropped", "not a finite colour")
		} else {
			var cc [3]float64
			changed := false
			for i := range c {
				cc[i] = clampF(c[i], ColorChannelMin, ColorChannelMax)
				changed = changed || cc[i] != c[i]
			}
			if changed {
				note("color", fmtColor(c), fmtColor(cc), "colour channels limited to 0–1")
			}
			out.Color = &cc
		}
	}

	out.UseSquare = in.UseSquare

	if in.Temperature != nil || in.UseTemperature != nil {
		if caps.Temperature {
			out.Temperature = num("temperature", in.Temperature, TemperatureMin, TemperatureMax, "Blender's temperature range is 800–20000 K")
			out.UseTemperature = in.UseTemperature
			if out.Temperature != nil && out.UseTemperature == nil {
				t := true
				out.UseTemperature = &t // a proposed temperature only takes effect when enabled
			}
		} else if in.Temperature != nil && finite(*in.Temperature) {
			// Blender before 4.5 has no temperature property: express the
			// requested temperature as an RGB colour instead, deterministically.
			k := clampF(*in.Temperature, TemperatureMin, TemperatureMax)
			if out.Color == nil {
				rgb := KelvinToRGB(k)
				out.Color = &rgb
				note("temperature", f(*in.Temperature)+" K", "color "+fmtColor(rgb), "this Blender version has no colour temperature; converted to RGB")
			} else {
				note("temperature", f(*in.Temperature)+" K", "dropped", "this Blender version has no colour temperature and a colour was also proposed")
			}
		} else if in.Temperature != nil {
			note("temperature", "non-finite", "dropped", "not a finite number")
		}
	}

	if in.Preset != nil {
		id := strings.TrimSpace(*in.Preset)
		switch {
		case cur.Preset == "custom":
			note("preset", id, "dropped", "this light has a hand-built node tree; the addon never rewires it")
		case id == "none" || validPreset(id):
			out.Preset = &id
		default:
			note("preset", id, "dropped", "not in the bundled gobo/IES library")
		}
	}

	return dropNoOps(cur, out), adj
}

const noopEps = 1e-6

func same(a, b float64) bool { return math.Abs(a-b) <= noopEps*math.Max(1, math.Abs(b)) }

func dropNoOps(cur Light, p Proposal) Proposal {
	if p.SpotSize != nil && same(*p.SpotSize, cur.SpotSize) {
		p.SpotSize = nil
	}
	if p.SpotBlend != nil && same(*p.SpotBlend, cur.SpotBlend) {
		p.SpotBlend = nil
	}
	if p.Energy != nil && same(*p.Energy, cur.Energy) {
		p.Energy = nil
	}
	if p.ShadowSoftSize != nil && same(*p.ShadowSoftSize, cur.ShadowSoftSize) {
		p.ShadowSoftSize = nil
	}
	if p.Color != nil && same(p.Color[0], cur.Color[0]) && same(p.Color[1], cur.Color[1]) && same(p.Color[2], cur.Color[2]) {
		p.Color = nil
	}
	if p.UseSquare != nil && *p.UseSquare == cur.UseSquare {
		p.UseSquare = nil
	}
	if p.Temperature != nil && same(*p.Temperature, cur.Temperature) {
		p.Temperature = nil
	}
	if p.UseTemperature != nil && *p.UseTemperature == cur.UseTemperature {
		p.UseTemperature = nil
	}
	if p.Preset != nil && *p.Preset == cur.Preset {
		p.Preset = nil
	}
	if p.Size != nil && same(*p.Size, cur.Size) {
		p.Size = nil
	}
	if p.SizeY != nil && same(*p.SizeY, cur.SizeY) {
		p.SizeY = nil
	}
	if p.Spread != nil && same(*p.Spread, cur.Spread) {
		p.Spread = nil
	}
	if p.Snoot != nil && *p.Snoot == cur.Snoot {
		p.Snoot = nil
	}
	if cur.Snoot && p.SnootLength != nil && same(*p.SnootLength, cur.SnootLength) {
		p.SnootLength = nil
	}
	if cur.Snoot && p.SnootMouth != nil && same(*p.SnootMouth, cur.SnootMouth) {
		p.SnootMouth = nil
	}
	return p
}

func fmtColor(c [3]float64) string { return fmt.Sprintf("(%.3f, %.3f, %.3f)", c[0], c[1], c[2]) }

// KelvinToRGB approximates a black-body colour (Tanner Helland's fit),
// normalised so the brightest channel is 1. Used only when the artist's
// Blender has no temperature property.
func KelvinToRGB(k float64) [3]float64 {
	t := k / 100
	var r, g, b float64
	if t <= 66 {
		r = 255
		g = 99.4708025861*math.Log(t) - 161.1195681661
	} else {
		r = 329.698727446 * math.Pow(t-60, -0.1332047592)
		g = 288.1221695283 * math.Pow(t-60, -0.0755148492)
	}
	switch {
	case t >= 66:
		b = 255
	case t <= 19:
		b = 0
	default:
		b = 138.5177312231*math.Log(t-10) - 305.0447927307
	}
	rgb := [3]float64{clampF(r, 0, 255) / 255, clampF(g, 0, 255) / 255, clampF(b, 0, 255) / 255}
	// The fit is in display (sRGB) values; Blender light colours are linear.
	for i, c := range rgb {
		if c <= 0.04045 {
			rgb[i] = c / 12.92
		} else {
			rgb[i] = math.Pow((c+0.055)/1.055, 2.4)
		}
	}
	m := math.Max(rgb[0], math.Max(rgb[1], rgb[2]))
	for i := range rgb {
		rgb[i] = math.Round(rgb[i]/m*1000) / 1000
	}
	return rgb
}
