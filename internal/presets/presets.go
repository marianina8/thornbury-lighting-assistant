// Package presets is the bundled gobo/IES library. presets.json is generated
// by tools/gen_presets.py and copied verbatim into the addon, so the backend
// can only ever suggest a preset the artist's addon actually ships.
package presets

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

//go:embed presets.json
var raw []byte

// Preset is one entry in the library.
type Preset struct {
	ID        string `json:"id"`
	Kind      string `json:"kind"` // "gobo" or "ies"
	Label     string `json:"label"`
	File      string `json:"file"`
	Extension string `json:"extension,omitempty"`
	Family    string `json:"family,omitempty"`
	UseWhen   string `json:"use_when"`
}

type doc struct {
	Version int      `json:"version"`
	Presets []Preset `json:"presets"`
}

var byID map[string]Preset
var ordered []Preset

func init() {
	var d doc
	if err := json.Unmarshal(raw, &d); err != nil {
		panic(fmt.Sprintf("presets.json: %v", err))
	}
	byID = make(map[string]Preset, len(d.Presets))
	for _, p := range d.Presets {
		byID[p.ID] = p
	}
	ordered = d.Presets
}

// Valid reports whether id is a preset in the library.
func Valid(id string) bool { _, ok := byID[id]; return ok }

// All returns the presets in library order.
func All() []Preset { return append([]Preset(nil), ordered...) }

// IDs returns every preset id, sorted.
func IDs() []string {
	ids := make([]string, 0, len(byID))
	for id := range byID {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

// PromptList renders the library for the model prompt.
func PromptList() string {
	var b strings.Builder
	for _, p := range ordered {
		fmt.Fprintf(&b, "- %s (%s): %s\n", p.ID, p.Kind, p.UseWhen)
	}
	return b.String()
}
