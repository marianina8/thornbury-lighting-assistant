// Command local runs the API on your machine with an in-memory store, so the
// Blender addon can be tried end to end before anything is deployed.
//
//	go run ./cmd/local                      # mock model, no AWS needed
//	go run ./cmd/local -model bedrock       # real Bedrock via your AWS profile
//
// It mints a key at startup and prints it; paste it into the addon's
// preferences with backend URL http://127.0.0.1:8787.
package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime"

	"github.com/marianina8/thornbury-lighting-assistant/internal/api"
	"github.com/marianina8/thornbury-lighting-assistant/internal/awsenv"
	"github.com/marianina8/thornbury-lighting-assistant/internal/keys"
	"github.com/marianina8/thornbury-lighting-assistant/internal/killswitch"
	"github.com/marianina8/thornbury-lighting-assistant/internal/propose"
	"github.com/marianina8/thornbury-lighting-assistant/internal/store"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:8787", "listen address")
	model := flag.String("model", "mock", "mock | bedrock")
	modelID := flag.String("model-id", propose.DefaultModelID, "Bedrock model or inference-profile id")
	profile := flag.String("profile", awsenv.DefaultProfile, "AWS profile (bedrock mode)")
	region := flag.String("region", awsenv.DefaultRegion, "AWS region (bedrock mode)")
	limit := flag.Int("monthly-limit", 50, "monthly limit for the printed key")
	fixedKey := flag.String("key", "", "use this key instead of minting one (tests)")
	flag.Parse()

	ctx := context.Background()
	var m propose.Model = &propose.Mock{}
	if *model == "bedrock" {
		cfg, err := awsenv.Load(ctx, *profile, *region)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		m = &propose.Bedrock{Client: bedrockruntime.NewFromConfig(cfg), ModelID: *modelID}
	}

	st := store.NewMemory()
	k := *fixedKey
	if k == "" {
		k, _ = keys.Generate()
	}
	h := keys.Hash(k)
	now := time.Now()
	_ = st.PutKey(ctx, store.Key{Hash: h, ID: keys.ID(h), Prefix: keys.Display(k), Label: "local", MonthlyLimit: *limit,
		Active: true, Period: store.Period(now), CreatedAt: now.UTC().Format(time.RFC3339)})

	srv := &api.Server{Store: st, Model: m, Switch: killswitch.Static(true),
		Log: slog.New(slog.NewTextHandler(os.Stderr, nil)), GlobalDailyLimit: 1000,
		AuditRetention: 24 * time.Hour, ModelTimeout: 30 * time.Second}
	mux := http.NewServeMux()
	mux.Handle("/v1/", srv.Handler())
	// Local-only: dump the audit trail so you can see what was recorded.
	mux.HandleFunc("GET /local/audit", func(w http.ResponseWriter, r *http.Request) {
		as, _ := st.RecentAudit(r.Context(), "", 100)
		w.Header().Set("Content-Type", "application/json")
		_ = jsonEncode(w, as)
	})

	fmt.Printf("\nThornbury Lighting Assistant (local, model=%s)\n", m.ID())
	fmt.Printf("  Backend URL: http://%s\n  API key:     %s\n  Audit trail: http://%s/local/audit\n\n", *addr, k, *addr)
	if err := http.ListenAndServe(*addr, mux); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
