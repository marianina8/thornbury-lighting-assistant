package keys

import "testing"

func TestGenerateIsWellFormedAndUnique(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 200; i++ {
		k, err := Generate()
		if err != nil {
			t.Fatal(err)
		}
		if !WellFormed(k) {
			t.Fatalf("not well formed: %q", k)
		}
		if seen[k] {
			t.Fatal("duplicate key")
		}
		seen[k] = true
	}
}

func TestHashIsStableAndNotThePlaintext(t *testing.T) {
	k, _ := Generate()
	if Hash(k) != Hash(k) || Hash(k) == k || len(Hash(k)) != 64 {
		t.Fatal("hash wrong")
	}
	if len(ID(Hash(k))) != 16 {
		t.Fatal("id wrong")
	}
}

func TestWellFormedRejects(t *testing.T) {
	for _, s := range []string{"", "tla_", "sk_" + string(make([]byte, 52)), "tla_ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ", "tla_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"} {
		if WellFormed(s) {
			t.Fatalf("accepted %q", s)
		}
	}
}
