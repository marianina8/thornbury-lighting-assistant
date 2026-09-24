package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-lambda-go/events"

	"github.com/marianina8/thornbury-lighting-assistant/internal/keys"
	"github.com/marianina8/thornbury-lighting-assistant/internal/killswitch"
	"github.com/marianina8/thornbury-lighting-assistant/internal/lightparams"
	"github.com/marianina8/thornbury-lighting-assistant/internal/propose"
	"github.com/marianina8/thornbury-lighting-assistant/internal/store"
)

var now = time.Date(2026, 9, 24, 18, 0, 0, 0, time.UTC)

type countingModel struct {
	inner propose.Model
	calls int
}

func (c *countingModel) ID() string { return c.inner.ID() }
func (c *countingModel) Propose(ctx context.Context, in propose.Input) (propose.Output, propose.Usage, error) {
	c.calls++
	return c.inner.Propose(ctx, in)
}

type env struct {
	srv   *Server
	st    *store.Memory
	model *countingModel
	sw    *bool
	key   string
	h     http.Handler
}

type sw struct{ on *bool }

func (s sw) Enabled(context.Context) bool { return *s.on }

func setup(t *testing.T, limit int, m propose.Model) *env {
	t.Helper()
	st := store.NewMemory()
	k, _ := keys.Generate()
	h := keys.Hash(k)
	_ = st.PutKey(context.Background(), store.Key{Hash: h, ID: keys.ID(h), Prefix: keys.Display(k), Label: "boyfriend-tester",
		MonthlyLimit: limit, Active: true, Period: store.Period(now), CreatedAt: now.Format(time.RFC3339)})
	on := true
	if m == nil {
		m = &propose.Mock{}
	}
	cm := &countingModel{inner: m}
	s := &Server{Store: st, Model: cm, Switch: sw{&on}, Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Now: func() time.Time { return now }, GlobalDailyLimit: 100, AuditRetention: 180 * 24 * time.Hour, ModelTimeout: time.Second}
	return &env{srv: s, st: st, model: cm, sw: &on, key: k, h: s.Handler()}
}

func light() lightparams.Light {
	return lightparams.Light{Name: "Key", Type: "SPOT", SpotSize: math.Pi / 4, SpotBlend: 0.15, Energy: 1000,
		Color: [3]float64{1, 0.85, 0.7}, ShadowSoftSize: 0.25, Preset: "none"}
}

func (e *env) do(method, path, key string, body any) (*httptest.ResponseRecorder, map[string]any) {
	var buf bytes.Buffer
	if s, ok := body.(string); ok {
		buf.WriteString(s)
	} else if body != nil {
		_ = json.NewEncoder(&buf).Encode(body)
	}
	req := httptest.NewRequest(method, path, &buf)
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	rec := httptest.NewRecorder()
	e.h.ServeHTTP(rec, req)
	var out map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &out)
	return rec, out
}

func suggestBody(note string) SuggestRequest {
	return SuggestRequest{Note: note, Light: light(), BlenderVersion: "5.2.2", ClientVersion: "0.1.0"}
}

func TestSuggestHappyPathAuditsAndCounts(t *testing.T) {
	e := setup(t, 50, nil)
	rec, out := e.do("POST", "/v1/suggest", e.key, suggestBody("snoot the key down so it stops spilling on the background, keep it warm"))
	if rec.Code != 200 {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
	prop := out["proposal"].(map[string]any)
	if prop["spot_size"].(float64) >= math.Pi/4 {
		t.Fatal("cone should narrow")
	}
	if _, ok := prop["color"]; ok {
		t.Fatal("keep it warm: colour must not change")
	}
	if out["usage"].(map[string]any)["used"].(float64) != 1 {
		t.Fatal("usage not reported")
	}
	id := out["request_id"].(string)
	a, _ := e.st.GetAudit(context.Background(), id)
	if a == nil || a.Status != store.StatusProposed || a.Note == "" || a.Current == "" || a.ModelOutput == "" || a.Label != "boyfriend-tester" {
		t.Fatalf("audit incomplete: %+v", a)
	}
	if strings.Contains(a.KeyID+a.Label+a.Current, e.key) {
		t.Fatal("plaintext key leaked into the audit trail")
	}
	// Outcome: edited, with applied values.
	rec, _ = e.do("POST", "/v1/outcome", e.key, map[string]any{"request_id": id, "outcome": "edited", "applied_values": map[string]any{"spot_size": 0.4, "bogus": 1}})
	if rec.Code != 200 {
		t.Fatalf("outcome %d %s", rec.Code, rec.Body)
	}
	a, _ = e.st.GetAudit(context.Background(), id)
	if a.Outcome != "edited" || !strings.Contains(a.AppliedValues, "0.4") || strings.Contains(a.AppliedValues, "bogus") {
		t.Fatalf("outcome stored wrong: %+v", a)
	}
	rec, _ = e.do("POST", "/v1/outcome", e.key, map[string]any{"request_id": id, "outcome": "applied"})
	if rec.Code != http.StatusConflict {
		t.Fatalf("second outcome should conflict: %d", rec.Code)
	}
}

func TestOutcomeCannotBeSetByAnotherKey(t *testing.T) {
	e := setup(t, 50, nil)
	_, out := e.do("POST", "/v1/suggest", e.key, suggestBody("tighten it"))
	other, _ := keys.Generate()
	oh := keys.Hash(other)
	_ = e.st.PutKey(context.Background(), store.Key{Hash: oh, ID: keys.ID(oh), Label: "other", MonthlyLimit: 5, Active: true, Period: store.Period(now)})
	rec, _ := e.do("POST", "/v1/outcome", other, map[string]any{"request_id": out["request_id"], "outcome": "discarded"})
	if rec.Code != http.StatusNotFound {
		t.Fatalf("got %d", rec.Code)
	}
}

func TestAuthFailuresNeverCallTheModel(t *testing.T) {
	e := setup(t, 50, nil)
	for _, k := range []string{"", "nonsense", "tla_" + strings.Repeat("a", 52)} {
		rec, _ := e.do("POST", "/v1/suggest", k, suggestBody("tighten"))
		if rec.Code != http.StatusUnauthorized {
			t.Fatalf("key %q: %d", k, rec.Code)
		}
	}
	_ = e.st.RevokeKey(context.Background(), keys.Hash(e.key), now)
	rec, out := e.do("POST", "/v1/suggest", e.key, suggestBody("tighten"))
	if rec.Code != http.StatusForbidden || out["code"] != "revoked" {
		t.Fatalf("revoked: %d %v", rec.Code, out)
	}
	if e.model.calls != 0 {
		t.Fatalf("model called %d times", e.model.calls)
	}
}

func TestMonthlyQuota(t *testing.T) {
	e := setup(t, 2, nil)
	for i := 0; i < 2; i++ {
		if rec, _ := e.do("POST", "/v1/suggest", e.key, suggestBody("tighten")); rec.Code != 200 {
			t.Fatalf("call %d: %d", i, rec.Code)
		}
	}
	rec, out := e.do("POST", "/v1/suggest", e.key, suggestBody("tighten"))
	if rec.Code != http.StatusTooManyRequests || !strings.Contains(out["error"].(string), "2026-10-01") {
		t.Fatalf("quota: %d %v", rec.Code, out)
	}
	if e.model.calls != 2 {
		t.Fatalf("model calls %d", e.model.calls)
	}
	// /v1/me is free and reports usage.
	rec, out = e.do("GET", "/v1/me", e.key, nil)
	if rec.Code != 200 || out["usage"].(map[string]any)["used"].(float64) != 2 {
		t.Fatalf("me: %v", out)
	}
}

func TestKillSwitchStopsEverythingBeforeAuth(t *testing.T) {
	e := setup(t, 50, nil)
	*e.sw = false
	rec, out := e.do("POST", "/v1/suggest", e.key, suggestBody("tighten"))
	if rec.Code != http.StatusServiceUnavailable || out["code"] != "paused" || e.model.calls != 0 {
		t.Fatalf("kill switch: %d %v", rec.Code, out)
	}
	k, _ := e.st.GetKey(context.Background(), keys.Hash(e.key))
	if k.Used != 0 {
		t.Fatal("paused request was counted")
	}
	_, out = e.do("GET", "/v1/health", "", nil)
	if out["enabled"] != false {
		t.Fatal("health should report paused")
	}
}

func TestGlobalCapRefundsTheKey(t *testing.T) {
	e := setup(t, 50, nil)
	e.srv.GlobalDailyLimit = 1
	e.do("POST", "/v1/suggest", e.key, suggestBody("tighten"))
	rec, out := e.do("POST", "/v1/suggest", e.key, suggestBody("tighten"))
	if rec.Code != http.StatusServiceUnavailable || out["code"] != "capacity" {
		t.Fatalf("%d %v", rec.Code, out)
	}
	k, _ := e.st.GetKey(context.Background(), keys.Hash(e.key))
	if k.Used != 1 {
		t.Fatalf("key should only be charged for the call that ran, used=%d", k.Used)
	}
}

func TestModelErrorIsRefundedAndAudited(t *testing.T) {
	e := setup(t, 50, &propose.Mock{Err: errors.New("throttled")})
	rec, out := e.do("POST", "/v1/suggest", e.key, suggestBody("tighten"))
	if rec.Code != http.StatusBadGateway || out["code"] != "model_error" {
		t.Fatalf("%d %v", rec.Code, out)
	}
	k, _ := e.st.GetKey(context.Background(), keys.Hash(e.key))
	if k.Used != 0 {
		t.Fatal("failed call was charged")
	}
	as, _ := e.st.RecentAudit(context.Background(), "", 5)
	if len(as) != 1 || as[0].Status != store.StatusModelError || as[0].Error == "" {
		t.Fatalf("model error not audited: %+v", as)
	}
}

func TestBadInputIsRejectedBeforeCharging(t *testing.T) {
	e := setup(t, 50, nil)
	cases := []any{
		"{not json",
		suggestBody(""),
		suggestBody(strings.Repeat("x", propose.MaxNoteRunes+1)),
		func() SuggestRequest { r := suggestBody("x"); r.Light.Type = "POINT"; return r }(),
		func() SuggestRequest { r := suggestBody("x"); r.Light.SpotBlend = 3; return r }(),
		`{"note":"` + strings.Repeat("a", MaxBodyBytes) + `"}`,
	}
	for i, c := range cases {
		rec, _ := e.do("POST", "/v1/suggest", e.key, c)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("case %d: %d %s", i, rec.Code, rec.Body)
		}
	}
	k, _ := e.st.GetKey(context.Background(), keys.Hash(e.key))
	if k.Used != 0 || e.model.calls != 0 {
		t.Fatal("rejected requests must not be charged or reach the model")
	}
}

func TestHostileModelOutputIsClamped(t *testing.T) {
	yes := true
	cone, power, blend := 720.0, 1e9, -3.0
	preset := "gobo_from_the_internet"
	e := setup(t, 50, &propose.Mock{Raw: &propose.Output{InScope: &yes, ConeAngleDeg: &cone, PowerW: &power, Blend: &blend,
		ColorRGB: []float64{5, -1, 0.5}, Preset: &preset, Rationale: strings.Repeat("long ", 200)}})
	rec, out := e.do("POST", "/v1/suggest", e.key, suggestBody("do whatever"))
	if rec.Code != 200 {
		t.Fatal(rec.Body)
	}
	p := out["proposal"].(map[string]any)
	if p["spot_size"].(float64) > math.Pi || p["energy"].(float64) != 4000 || p["spot_blend"].(float64) != 0 {
		t.Fatalf("not clamped: %v", p)
	}
	if _, ok := p["preset"]; ok {
		t.Fatal("unknown preset leaked")
	}
	c := p["color"].([]any)
	if c[0].(float64) != 1 || c[1].(float64) != 0 {
		t.Fatalf("colour not clamped: %v", c)
	}
	if len([]rune(out["rationale"].(string))) > propose.MaxRationaleRunes {
		t.Fatal("rationale not bounded")
	}
	if len(out["adjustments"].([]any)) < 5 {
		t.Fatalf("adjustments not reported: %v", out["adjustments"])
	}
}

func TestOutOfScopeIsAuditedAndCarriesNothing(t *testing.T) {
	e := setup(t, 50, nil)
	rec, out := e.do("POST", "/v1/suggest", e.key, suggestBody("move the key to camera left"))
	if rec.Code != 200 || out["in_scope"] != false {
		t.Fatalf("%v", out)
	}
	if len(out["proposal"].(map[string]any)) != 0 {
		t.Fatal("out-of-scope must carry no changes")
	}
	rec, _ = e.do("POST", "/v1/outcome", e.key, map[string]any{"request_id": out["request_id"], "outcome": "applied"})
	if rec.Code != http.StatusConflict {
		t.Fatal("cannot apply an out-of-scope answer")
	}
}

func TestLambdaAdapter(t *testing.T) {
	e := setup(t, 50, nil)
	b, _ := json.Marshal(suggestBody("tighten"))
	ev := events.APIGatewayV2HTTPRequest{RawPath: "/v1/suggest", Body: string(b),
		Headers: map[string]string{"authorization": "Bearer " + e.key, "content-type": "application/json"}}
	ev.RequestContext.HTTP.Method = "POST"
	resp, err := LambdaAdapter(e.h)(context.Background(), ev)
	if err != nil || resp.StatusCode != 200 || !strings.Contains(resp.Body, "request_id") {
		t.Fatalf("%v %d %s", err, resp.StatusCode, resp.Body)
	}
	if resp.Headers["Content-Type"] != "application/json" {
		t.Fatalf("headers: %v", resp.Headers)
	}
}

var _ killswitch.Switch = sw{}
