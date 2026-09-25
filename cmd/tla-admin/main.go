// Command tla-admin manages keys, the kill switch and the audit trail.
//
//	tla-admin keys                          list keys and this month's usage
//	tla-admin revoke --id <key id>          revoke a key
//	tla-admin set-limit --id <id> --limit N change a key's monthly limit
//	tla-admin audit [--id <key id>] [--n 20] recent suggestions and outcomes
//	tla-admin pause | resume | status       the global kill switch
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/cloudwatch"
	cwtypes "github.com/aws/aws-sdk-go-v2/service/cloudwatch/types"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/ssm"

	"github.com/marianina8/thornbury-lighting-assistant/internal/awsenv"
	"github.com/marianina8/thornbury-lighting-assistant/internal/killswitch"
	"github.com/marianina8/thornbury-lighting-assistant/internal/store"
)

func usage() {
	fmt.Fprintln(os.Stderr, `usage: tla-admin <keys|revoke|set-limit|audit|pause|resume|status> [flags]
common flags: --stack thornbury-lighting-assistant --profile <aws profile> --region us-west-2`)
	os.Exit(2)
}

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	cmd := os.Args[1]
	fs := flag.NewFlagSet(cmd, flag.ExitOnError)
	stack := fs.String("stack", awsenv.DefaultStack, "stack name")
	profile := fs.String("profile", awsenv.DefaultProfile, "AWS profile")
	region := fs.String("region", awsenv.DefaultRegion, "AWS region")
	id := fs.String("id", "", "key id (from `tla-admin keys`)")
	limit := fs.Int("limit", -1, "monthly limit")
	n := fs.Int("n", 20, "how many audit rows")
	asJSON := fs.Bool("json", false, "JSON output (audit)")
	_ = fs.Parse(os.Args[2:])

	ctx := context.Background()
	cfg, err := awsenv.Load(ctx, *profile, *region)
	check(err)
	outs, err := awsenv.StackOutputs(ctx, cfg, *stack)
	check(err)
	check(awsenv.Require(outs, "KeysTable", "AuditTable", "KillSwitchParam"))
	st := &store.Dynamo{DB: dynamodb.NewFromConfig(cfg), KeysTable: outs["KeysTable"], AuditTable: outs["AuditTable"]}
	sc := ssm.NewFromConfig(cfg)
	now := time.Now()

	switch cmd {
	case "keys":
		ks, err := st.ListKeys(ctx)
		check(err)
		w := tabwriter.NewWriter(os.Stdout, 0, 2, 2, ' ', 0)
		fmt.Fprintln(w, "ID\tLABEL\tKEY\tACTIVE\tUSED THIS MONTH\tLIMIT\tLAST USED")
		for _, k := range ks {
			fmt.Fprintf(w, "%s\t%s\t%s\t%v\t%d\t%d\t%s\n", k.ID, k.Label, k.Prefix, k.Active, k.UsedIn(store.Period(now)), k.MonthlyLimit, k.LastUsedAt)
		}
		w.Flush()
	case "revoke", "set-limit":
		k := findKey(ctx, st, *id)
		if cmd == "revoke" {
			check(st.RevokeKey(ctx, k.Hash, now))
			fmt.Printf("revoked %s (%s)\n", k.ID, k.Label)
		} else {
			if *limit < 0 {
				check(fmt.Errorf("--limit is required"))
			}
			check(st.SetKeyLimit(ctx, k.Hash, *limit))
			fmt.Printf("%s (%s) monthly limit is now %d\n", k.ID, k.Label, *limit)
		}
	case "audit":
		as, err := st.RecentAudit(ctx, *id, *n)
		check(err)
		if *asJSON {
			e := json.NewEncoder(os.Stdout)
			e.SetIndent("", "  ")
			check(e.Encode(as))
			return
		}
		w := tabwriter.NewWriter(os.Stdout, 0, 2, 2, ' ', 0)
		fmt.Fprintln(w, "WHEN (UTC)\tLABEL\tSTATUS\tOUTCOME\tCONF\tNOTE\tPROPOSAL")
		for _, a := range as {
			fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%.2f\t%s\t%s\n", a.CreatedAt[:19], a.Label, a.Status, dash(a.Outcome), a.Confidence, clip(a.Note, 50), clip(a.Proposal, 70))
		}
		w.Flush()
	case "pause", "resume":
		check(killswitch.Set(ctx, sc, outs["KillSwitchParam"], cmd == "resume"))
		if cmd == "resume" && outs["ModelCallsAlarmName"] != "" {
			// An alarm only acts when its state changes. If it paused the
			// assistant and is still in ALARM, reset it so it can fire again.
			_, err := cloudwatch.NewFromConfig(cfg).SetAlarmState(ctx, &cloudwatch.SetAlarmStateInput{
				AlarmName: aws.String(outs["ModelCallsAlarmName"]), StateValue: cwtypes.StateValueOk,
				StateReason: aws.String("reset by tla-admin resume"),
			})
			check(err)
		}
		fmt.Printf("assistant %sd (takes effect within 30 s)\n", cmd)
	case "status":
		out, err := sc.GetParameter(ctx, &ssm.GetParameterInput{Name: aws.String(outs["KillSwitchParam"])})
		check(err)
		fmt.Printf("enabled=%s  api=%s\n", aws.ToString(out.Parameter.Value), outs["ApiUrl"])
	default:
		usage()
	}
}

func findKey(ctx context.Context, st *store.Dynamo, id string) store.Key {
	if id == "" {
		check(fmt.Errorf("--id is required (see `tla-admin keys`)"))
	}
	ks, err := st.ListKeys(ctx)
	check(err)
	for _, k := range ks {
		if k.ID == id {
			return k
		}
	}
	check(fmt.Errorf("no key with id %s", id))
	return store.Key{}
}

func dash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func clip(s string, n int) string {
	s = strings.ReplaceAll(s, "\n", " ")
	if r := []rune(s); len(r) > n {
		return string(r[:n-1]) + "…"
	}
	return s
}

func check(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "tla-admin:", err)
		os.Exit(1)
	}
}
