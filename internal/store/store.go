// Package store holds API keys, usage counters and the audit trail.
// Memory is used by tests and cmd/local; Dynamo is used in AWS. Both give the
// same guarantees: quota reservation is atomic, a key's plaintext is never
// stored, and an outcome can be recorded once, by the key that made the
// request.
package store

import (
	"context"
	"errors"
	"time"
)

// Key is one issued API key (hash only).
type Key struct {
	Hash         string `dynamodbav:"key_hash" json:"-"`
	ID           string `dynamodbav:"key_id" json:"id"`
	Prefix       string `dynamodbav:"key_prefix" json:"prefix"`
	Label        string `dynamodbav:"label" json:"label"`
	MonthlyLimit int    `dynamodbav:"monthly_limit" json:"monthly_limit"`
	Used         int    `dynamodbav:"used" json:"used"`
	Period       string `dynamodbav:"period" json:"period"` // "2026-09" (UTC)
	Active       bool   `dynamodbav:"active" json:"active"`
	CreatedAt    string `dynamodbav:"created_at" json:"created_at"`
	RevokedAt    string `dynamodbav:"revoked_at,omitempty" json:"revoked_at,omitempty"`
	LastUsedAt   string `dynamodbav:"last_used_at,omitempty" json:"last_used_at,omitempty"`
}

// UsedIn is the count for the given period (a new month starts at zero).
func (k Key) UsedIn(period string) int {
	if k.Period != period {
		return 0
	}
	return k.Used
}

// Audit is one suggestion request and what happened to it.
type Audit struct {
	RequestID      string  `dynamodbav:"request_id" json:"request_id"`
	KeyID          string  `dynamodbav:"key_id" json:"key_id"`
	Label          string  `dynamodbav:"label" json:"label"`
	CreatedAt      string  `dynamodbav:"created_at" json:"created_at"`
	ExpiresAt      int64   `dynamodbav:"expires_at" json:"-"`
	Note           string  `dynamodbav:"note" json:"note"`
	BlenderVersion string  `dynamodbav:"blender_version,omitempty" json:"blender_version,omitempty"`
	ClientVersion  string  `dynamodbav:"client_version,omitempty" json:"client_version,omitempty"`
	Current        string  `dynamodbav:"current" json:"current"` // JSON
	ModelID        string  `dynamodbav:"model_id" json:"model_id"`
	ModelOutput    string  `dynamodbav:"model_output,omitempty" json:"model_output,omitempty"` // raw JSON from the model
	Proposal       string  `dynamodbav:"proposal,omitempty" json:"proposal,omitempty"`         // JSON after clamping
	Adjustments    string  `dynamodbav:"adjustments,omitempty" json:"adjustments,omitempty"`   // JSON
	Rationale      string  `dynamodbav:"rationale,omitempty" json:"rationale,omitempty"`
	Confidence     float64 `dynamodbav:"confidence" json:"confidence"`
	InScope        bool    `dynamodbav:"in_scope" json:"in_scope"`
	Status         string  `dynamodbav:"status" json:"status"` // proposed | no_change | out_of_scope | model_error
	Error          string  `dynamodbav:"error,omitempty" json:"error,omitempty"`
	InputTokens    int     `dynamodbav:"input_tokens" json:"input_tokens"`
	OutputTokens   int     `dynamodbav:"output_tokens" json:"output_tokens"`
	LatencyMS      int64   `dynamodbav:"latency_ms" json:"latency_ms"`
	Outcome        string  `dynamodbav:"outcome,omitempty" json:"outcome,omitempty"` // applied | discarded | edited
	OutcomeAt      string  `dynamodbav:"outcome_at,omitempty" json:"outcome_at,omitempty"`
	AppliedValues  string  `dynamodbav:"applied_values,omitempty" json:"applied_values,omitempty"` // JSON
}

// Status values.
const (
	StatusProposed   = "proposed"
	StatusNoChange   = "no_change"
	StatusOutOfScope = "out_of_scope"
	StatusModelError = "model_error"
)

// Outcomes the addon can report.
var ValidOutcomes = map[string]bool{"applied": true, "discarded": true, "edited": true}

// Reserve results.
type Reservation int

const (
	Reserved Reservation = iota
	NoSuchKey
	Revoked
	QuotaExceeded
)

var (
	ErrNotFound = errors.New("not found")
	ErrConflict = errors.New("conflict")
	ErrExists   = errors.New("already exists")
)

// Store is everything the API and admin CLI need.
type Store interface {
	GetKey(ctx context.Context, hash string) (*Key, error) // nil, nil when absent
	// Reserve atomically counts one call against the key's monthly limit.
	Reserve(ctx context.Context, hash, period string, now time.Time) (Reservation, error)
	Refund(ctx context.Context, hash, period string) error
	// ReserveGlobal atomically counts one call against the whole service's daily cap.
	ReserveGlobal(ctx context.Context, day string, limit int, expires time.Time) (bool, error)
	RefundGlobal(ctx context.Context, day string) error
	PutAudit(ctx context.Context, a Audit) error
	// SetOutcome records applied/discarded/edited once, only for the key that
	// made the request and only for a request that proposed a change.
	SetOutcome(ctx context.Context, requestID, keyID, outcome, appliedJSON string, at time.Time) error
	GetAudit(ctx context.Context, requestID string) (*Audit, error)

	// Admin.
	PutKey(ctx context.Context, k Key) error
	ListKeys(ctx context.Context) ([]Key, error)
	SetKeyLimit(ctx context.Context, hash string, limit int) error
	RevokeKey(ctx context.Context, hash string, at time.Time) error
	RecentAudit(ctx context.Context, keyID string, limit int) ([]Audit, error)
}

// Period returns the monthly quota period for t ("2026-09", UTC).
func Period(t time.Time) string { return t.UTC().Format("2006-01") }

// Day returns the daily global-cap bucket for t ("2026-09-24", UTC).
func Day(t time.Time) string { return t.UTC().Format("2006-01-02") }
