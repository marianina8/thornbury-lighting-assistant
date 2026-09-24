// Command issue-key mints an API key for a tester and stores only its hash.
// The plaintext key is printed once and never stored anywhere by the backend.
//
//	issue-key --label "boyfriend-tester" --monthly-limit 50
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/service/dynamodb"

	"github.com/marianina8/thornbury-lighting-assistant/internal/awsenv"
	"github.com/marianina8/thornbury-lighting-assistant/internal/keys"
	"github.com/marianina8/thornbury-lighting-assistant/internal/store"
)

func main() {
	label := flag.String("label", "", "who this key is for (required), e.g. boyfriend-tester")
	limit := flag.Int("monthly-limit", 50, "suggestions per calendar month (UTC)")
	stack := flag.String("stack", awsenv.DefaultStack, "CloudFormation stack name")
	profile := flag.String("profile", awsenv.DefaultProfile, "AWS CLI profile")
	region := flag.String("region", awsenv.DefaultRegion, "AWS region")
	flag.Parse()

	*label = strings.TrimSpace(*label)
	if *label == "" || len(*label) > 64 {
		fmt.Fprintln(os.Stderr, "issue-key: --label is required (1-64 characters)")
		os.Exit(2)
	}
	if *limit < 1 || *limit > 10000 {
		fmt.Fprintln(os.Stderr, "issue-key: --monthly-limit must be 1-10000")
		os.Exit(2)
	}

	ctx := context.Background()
	cfg, err := awsenv.Load(ctx, *profile, *region)
	if err != nil {
		fail(err)
	}
	outs, err := awsenv.StackOutputs(ctx, cfg, *stack)
	if err != nil {
		fail(err)
	}
	if err := awsenv.Require(outs, "KeysTable", "ApiUrl"); err != nil {
		fail(err)
	}

	plain, err := keys.Generate()
	if err != nil {
		fail(err)
	}
	h := keys.Hash(plain)
	now := time.Now().UTC()
	st := &store.Dynamo{DB: dynamodb.NewFromConfig(cfg), KeysTable: outs["KeysTable"], AuditTable: outs["AuditTable"]}
	if err := st.PutKey(ctx, store.Key{Hash: h, ID: keys.ID(h), Prefix: keys.Display(plain), Label: *label,
		MonthlyLimit: *limit, Active: true, Period: store.Period(now), CreatedAt: now.Format(time.RFC3339)}); err != nil {
		fail(err)
	}

	fmt.Printf(`
Key issued for %q (id %s), %d suggestions per month.

  Backend URL:  %s
  API key:      %s

This is the only time the key is shown. Send both lines to the tester; in
Blender they go in Edit > Preferences > Add-ons > Thornbury Lighting Assistant.
Revoke it any time with: go run ./cmd/tla-admin revoke --id %s
`, *label, keys.ID(h), *limit, outs["ApiUrl"], plain, keys.ID(h))
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "issue-key:", err)
	os.Exit(1)
}
