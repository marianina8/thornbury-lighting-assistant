// Package keys mints and hashes API keys. Plaintext keys exist only at the
// moment they are minted (printed once by cmd/issue-key) and in the artist's
// Blender preferences; the backend stores and looks up SHA-256 hashes only.
// Keys carry 256 bits of randomness, so an unsalted hash is not guessable.
package keys

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base32"
	"encoding/hex"
	"strings"
)

// Prefix marks a Thornbury Lighting Assistant key.
const Prefix = "tla_"

var enc = base32.StdEncoding.WithPadding(base32.NoPadding)

// Generate returns a new plaintext key: "tla_" + 52 base32 characters.
func Generate() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return Prefix + strings.ToLower(enc.EncodeToString(b)), nil
}

// Hash is the lookup key stored in DynamoDB.
func Hash(plaintext string) string {
	s := sha256.Sum256([]byte(plaintext))
	return hex.EncodeToString(s[:])
}

// ID is a short, non-secret identifier for logs and the audit trail.
func ID(hash string) string {
	if len(hash) < 16 {
		return hash
	}
	return hash[:16]
}

// Display shows the start of a key so its owner can recognise it.
func Display(plaintext string) string {
	if len(plaintext) <= 12 {
		return plaintext
	}
	return plaintext[:12] + "…"
}

// WellFormed is a cheap syntax check before any database lookup.
func WellFormed(s string) bool {
	if !strings.HasPrefix(s, Prefix) || len(s) != len(Prefix)+52 {
		return false
	}
	for _, r := range s[len(Prefix):] {
		if !(r >= 'a' && r <= 'z' || r >= '2' && r <= '7') {
			return false
		}
	}
	return true
}
