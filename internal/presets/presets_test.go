package presets

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestLibraryLoads(t *testing.T) {
	if len(All()) != 27 {
		t.Fatalf("want 27 presets (24 gobos + 3 IES), got %d", len(All()))
	}
	if !Valid("gobo_soft_iris") || Valid("none") || Valid("") {
		t.Fatal("Valid is wrong")
	}
}

// The addon ships its own copy; it must be byte-identical to the one the
// backend embeds, and every file it names must exist.
func TestAddonCopyMatchesAndFilesExist(t *testing.T) {
	dir := filepath.Join("..", "..", "addon", "thornbury_lighting", "presets")
	addonCopy, err := os.ReadFile(filepath.Join(dir, "presets.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(addonCopy, raw) {
		t.Fatal("addon presets.json differs from internal/presets/presets.json; run tools/gen_presets.py")
	}
	for _, p := range All() {
		if _, err := os.Stat(filepath.Join(dir, p.File)); err != nil {
			t.Fatalf("%s: %v", p.ID, err)
		}
		if _, err := os.Stat(filepath.Join(dir, "thumbs", p.ID+".png")); err != nil {
			t.Fatalf("%s has no picker thumbnail (run tools/render_thumbs.py): %v", p.ID, err)
		}
		if p.Kind != "gobo" && p.Kind != "ies" {
			t.Fatalf("%s: bad kind %q", p.ID, p.Kind)
		}
	}
}
