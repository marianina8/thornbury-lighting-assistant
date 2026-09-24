// Package api is the HTTP surface the Blender addon talks to.
//
//	GET  /v1/health   no auth; is the service up and switched on
//	GET  /v1/me       auth; key label and this month's usage (no model call)
//	POST /v1/suggest  auth; note + current light -> one model call -> clamped proposal
//	POST /v1/outcome  auth; applied | discarded | edited, for the audit trail
//
// Order of checks in /v1/suggest, cheapest first: kill switch, key, request
// validation, per-key monthly quota, global daily cap, then the one model
// call. The global daily cap counts every model call attempt and is never
// refunded (it is the cost guardrail). A key is refunded only when a failed
// call can't have been billed (no tokens reported and no timeout), so testers
// aren't charged for our outages but a note that reliably breaks the model
// can't be replayed for free. Rejections are logged; model calls are audited.
package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/marianina8/thornbury-lighting-assistant/internal/keys"
	"github.com/marianina8/thornbury-lighting-assistant/internal/killswitch"
	"github.com/marianina8/thornbury-lighting-assistant/internal/lightparams"
	"github.com/marianina8/thornbury-lighting-assistant/internal/propose"
	"github.com/marianina8/thornbury-lighting-assistant/internal/store"
)

// MaxBodyBytes bounds every request body.
const MaxBodyBytes = 16 << 10

// Server holds the API's dependencies.
type Server struct {
	Store            store.Store
	Model            propose.Model
	Switch           killswitch.Switch
	Log              *slog.Logger
	Now              func() time.Time
	GlobalDailyLimit int
	AuditRetention   time.Duration
	ModelTimeout     time.Duration
	// Metric, if set, is called once per model call (the Lambda emits a
	// CloudWatch EMF metric that the usage alarm watches).
	Metric func(name string, value float64)
}

func (s *Server) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

// Handler returns the routed handler.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/health", s.health)
	mux.HandleFunc("GET /v1/me", s.me)
	mux.HandleFunc("POST /v1/suggest", s.suggest)
	mux.HandleFunc("POST /v1/outcome", s.outcome)
	return mux
}

type apiError struct {
	Error string `json:"error"`
	Code  string `json:"code"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, status int, code, msg string) {
	writeJSON(w, status, apiError{Error: msg, Code: code})
}

func newID() string {
	b := make([]byte, 12)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func bearer(r *http.Request) string {
	if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer ") {
		return strings.TrimSpace(strings.TrimPrefix(h, "Bearer "))
	}
	return strings.TrimSpace(r.Header.Get("X-Api-Key"))
}

// authenticate resolves the caller's key. It never distinguishes "malformed"
// from "unknown" to the caller.
func (s *Server) authenticate(w http.ResponseWriter, r *http.Request) (*store.Key, bool) {
	k := bearer(r)
	if !keys.WellFormed(k) {
		fail(w, http.StatusUnauthorized, "unauthorized", "Missing or invalid API key. Check the key in the addon's preferences.")
		return nil, false
	}
	rec, err := s.Store.GetKey(r.Context(), keys.Hash(k))
	if err != nil {
		s.Log.Error("get key", "err", err)
		fail(w, http.StatusInternalServerError, "internal", "Something went wrong on the server.")
		return nil, false
	}
	if rec == nil {
		fail(w, http.StatusUnauthorized, "unauthorized", "Missing or invalid API key. Check the key in the addon's preferences.")
		return nil, false
	}
	if !rec.Active {
		fail(w, http.StatusForbidden, "revoked", "This API key has been revoked. Ask for a new one.")
		return nil, false
	}
	return rec, true
}

func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, MaxBodyBytes)
	if err := json.NewDecoder(r.Body).Decode(v); err != nil {
		fail(w, http.StatusBadRequest, "bad_request", "The request body isn't valid JSON (or is larger than 16 KB).")
		return false
	}
	return true
}

func nextPeriodStart(t time.Time) string {
	y, m, _ := t.UTC().Date()
	return time.Date(y, m+1, 1, 0, 0, 0, 0, time.UTC).Format("2006-01-02")
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "service": "thornbury-lighting-assistant", "enabled": s.Switch.Enabled(r.Context())})
}

type usage struct {
	Used   int    `json:"used"`
	Limit  int    `json:"limit"`
	Period string `json:"period"`
	Resets string `json:"resets"`
}

func (s *Server) me(w http.ResponseWriter, r *http.Request) {
	k, ok := s.authenticate(w, r)
	if !ok {
		return
	}
	now := s.now()
	p := store.Period(now)
	writeJSON(w, http.StatusOK, map[string]any{
		"label": k.Label, "key": k.Prefix, "enabled": s.Switch.Enabled(r.Context()),
		"usage": usage{Used: k.UsedIn(p), Limit: k.MonthlyLimit, Period: p, Resets: nextPeriodStart(now)},
	})
}

// SuggestRequest is what the addon sends.
type SuggestRequest struct {
	Note           string                   `json:"note"`
	Light          lightparams.Light        `json:"light"`
	Capabilities   lightparams.Capabilities `json:"capabilities"`
	BlenderVersion string                   `json:"blender_version"`
	ClientVersion  string                   `json:"client_version"`
}

// SuggestResponse is what the addon shows the artist.
type SuggestResponse struct {
	RequestID string `json:"request_id"`
	propose.Result
	Model string `json:"model"`
	Usage usage  `json:"usage"`
}

func (s *Server) suggest(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	if !s.Switch.Enabled(ctx) {
		s.Log.Info("rejected", "reason", "paused")
		fail(w, http.StatusServiceUnavailable, "paused", "The lighting assistant is paused by its owner. Try again later.")
		return
	}
	k, ok := s.authenticate(w, r)
	if !ok {
		return
	}
	var req SuggestRequest
	if !decode(w, r, &req) {
		return
	}
	note, err := propose.CleanNote(req.Note)
	if err != nil {
		fail(w, http.StatusBadRequest, "bad_note", strings.ToUpper(err.Error()[:1])+err.Error()[1:]+".")
		return
	}
	if err := lightparams.ValidateCurrent(req.Light); err != nil {
		fail(w, http.StatusBadRequest, "bad_light", "Light state rejected: "+err.Error()+".")
		return
	}
	req.BlenderVersion = trunc(req.BlenderVersion, 40)
	req.ClientVersion = trunc(req.ClientVersion, 40)

	now := s.now()
	period := store.Period(now)
	switch res, err := s.Store.Reserve(ctx, k.Hash, period, now); {
	case err != nil:
		s.Log.Error("reserve", "err", err)
		fail(w, http.StatusInternalServerError, "internal", "Something went wrong on the server.")
		return
	case res == store.Revoked:
		fail(w, http.StatusForbidden, "revoked", "This API key has been revoked. Ask for a new one.")
		return
	case res == store.NoSuchKey:
		fail(w, http.StatusUnauthorized, "unauthorized", "Missing or invalid API key.")
		return
	case res == store.QuotaExceeded:
		s.Log.Info("rejected", "reason", "quota", "key_id", keys.ID(k.Hash))
		fail(w, http.StatusTooManyRequests, "quota", fmt.Sprintf("This key has used its %d suggestions for %s. The count resets on %s.", k.MonthlyLimit, period, nextPeriodStart(now)))
		return
	}
	day := store.Day(now)
	okGlobal, err := false, error(nil)
	if s.GlobalDailyLimit > 0 {
		okGlobal, err = s.Store.ReserveGlobal(ctx, day, s.GlobalDailyLimit, now.Add(72*time.Hour))
	}
	if err != nil || !okGlobal {
		_ = s.Store.Refund(ctx, k.Hash, period)
		if err != nil {
			s.Log.Error("reserve global", "err", err)
		}
		s.Log.Warn("rejected", "reason", "global_daily_cap", "key_id", keys.ID(k.Hash))
		fail(w, http.StatusServiceUnavailable, "capacity", "The assistant has reached today's overall limit. Try again tomorrow; this didn't count against your key.")
		return
	}

	in := propose.Input{Note: note, Light: req.Light, Caps: req.Capabilities}
	mctx, cancel := context.WithTimeout(ctx, s.ModelTimeout)
	start := time.Now()
	out, use, merr := s.Model.Propose(mctx, in)
	cancel()
	latency := time.Since(start).Milliseconds()
	if s.Metric != nil {
		s.Metric("ModelCalls", 1)
	}

	reqID := newID()
	curJSON, _ := json.Marshal(req.Light)
	audit := store.Audit{
		RequestID: reqID, KeyID: keys.ID(k.Hash), Label: k.Label, CreatedAt: now.UTC().Format(time.RFC3339Nano),
		ExpiresAt: now.Add(s.AuditRetention).Unix(), Note: note, BlenderVersion: req.BlenderVersion,
		ClientVersion: req.ClientVersion, Current: string(curJSON), ModelID: s.Model.ID(),
		InputTokens: use.InputTokens, OutputTokens: use.OutputTokens, LatencyMS: latency,
	}

	if merr != nil {
		billed := use.InputTokens > 0 || use.OutputTokens > 0 || errors.Is(merr, context.DeadlineExceeded)
		msg := "The model call failed, so there's no suggestion. This didn't count against your key; try again."
		if billed {
			msg = "The model's answer couldn't be used, so there's no suggestion. Try rewording the note."
		} else {
			_ = s.Store.Refund(ctx, k.Hash, period)
		}
		audit.Status, audit.Error = store.StatusModelError, trunc(merr.Error(), 500)
		s.saveAudit(ctx, audit)
		s.Log.Error("model call failed", "request_id", reqID, "billed", billed, "err", merr)
		fail(w, http.StatusBadGateway, "model_error", msg)
		return
	}

	result := propose.Interpret(in, out)
	rawOut, _ := json.Marshal(out)
	propJSON, _ := json.Marshal(result.Proposal)
	adjJSON, _ := json.Marshal(result.Adjustments)
	audit.ModelOutput, audit.Proposal, audit.Adjustments = string(rawOut), string(propJSON), string(adjJSON)
	audit.Rationale, audit.Confidence, audit.InScope = result.Rationale, result.Confidence, result.InScope
	switch {
	case !result.InScope:
		audit.Status = store.StatusOutOfScope
	case result.Proposal.Empty():
		audit.Status = store.StatusNoChange
	default:
		audit.Status = store.StatusProposed
	}
	s.saveAudit(ctx, audit)
	s.Log.Info("suggest", "request_id", reqID, "key_id", audit.KeyID, "status", audit.Status,
		"confidence", result.Confidence, "adjustments", len(result.Adjustments), "latency_ms", latency,
		"input_tokens", use.InputTokens, "output_tokens", use.OutputTokens)

	used := k.UsedIn(period) + 1
	writeJSON(w, http.StatusOK, SuggestResponse{
		RequestID: reqID, Result: result, Model: s.Model.ID(),
		Usage: usage{Used: used, Limit: k.MonthlyLimit, Period: period, Resets: nextPeriodStart(now)},
	})
}

// saveAudit never fails the request (the artist already paid for the call),
// but a failure is logged loudly.
func (s *Server) saveAudit(ctx context.Context, a store.Audit) {
	if err := s.Store.PutAudit(ctx, a); err != nil {
		s.Log.Error("AUDIT WRITE FAILED", "request_id", a.RequestID, "err", err)
	}
}

// OutcomeRequest reports what the artist did with a suggestion.
type OutcomeRequest struct {
	RequestID     string          `json:"request_id"`
	Outcome       string          `json:"outcome"`
	AppliedValues json.RawMessage `json:"applied_values,omitempty"`
}

func (s *Server) outcome(w http.ResponseWriter, r *http.Request) {
	k, ok := s.authenticate(w, r)
	if !ok {
		return
	}
	var req OutcomeRequest
	if !decode(w, r, &req) {
		return
	}
	if !store.ValidOutcomes[req.Outcome] || len(req.RequestID) != 24 {
		fail(w, http.StatusBadRequest, "bad_outcome", "outcome must be applied, discarded or edited, with a request_id.")
		return
	}
	applied := ""
	if req.Outcome != "discarded" && len(req.AppliedValues) > 0 {
		var p lightparams.Proposal
		if err := json.Unmarshal(req.AppliedValues, &p); err != nil {
			fail(w, http.StatusBadRequest, "bad_outcome", "applied_values must be a light proposal object.")
			return
		}
		b, _ := json.Marshal(p) // re-encode: only known fields are stored
		applied = string(b)
	}
	err := s.Store.SetOutcome(r.Context(), req.RequestID, keys.ID(k.Hash), req.Outcome, applied, s.now())
	switch {
	case errors.Is(err, store.ErrNotFound):
		fail(w, http.StatusNotFound, "not_found", "No such suggestion for this key.")
	case errors.Is(err, store.ErrConflict):
		fail(w, http.StatusConflict, "conflict", "An outcome was already recorded for this suggestion.")
	case err != nil:
		s.Log.Error("set outcome", "err", err)
		fail(w, http.StatusInternalServerError, "internal", "Something went wrong on the server.")
	default:
		s.Log.Info("outcome", "request_id", req.RequestID, "outcome", req.Outcome)
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	}
}

func trunc(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}
