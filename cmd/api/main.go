// Command api is the Lambda binary. With HANDLER=api (default) it serves the
// HTTP API; with HANDLER=killswitch it is the alarm target that turns the
// assistant off when usage spikes.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"strconv"
	"time"

	"github.com/aws/aws-lambda-go/events"
	"github.com/aws/aws-lambda-go/lambda"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/ssm"

	"github.com/marianina8/thornbury-lighting-assistant/internal/api"
	"github.com/marianina8/thornbury-lighting-assistant/internal/killswitch"
	"github.com/marianina8/thornbury-lighting-assistant/internal/propose"
	"github.com/marianina8/thornbury-lighting-assistant/internal/store"
)

func envInt(name string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(name)); err == nil {
		return v
	}
	return def
}

func must(name string) string {
	v := os.Getenv(name)
	if v == "" {
		panic("missing env " + name)
	}
	return v
}

// emf writes a CloudWatch Embedded Metric Format line; CloudWatch turns it
// into a metric with no extra API call.
func emf(name string, value float64) {
	b, _ := json.Marshal(map[string]any{
		"_aws": map[string]any{
			"Timestamp": time.Now().UnixMilli(),
			"CloudWatchMetrics": []any{map[string]any{
				"Namespace": "ThornburyLighting", "Dimensions": [][]string{{}},
				"Metrics": []any{map[string]any{"Name": name, "Unit": "Count"}},
			}},
		},
		name: value,
	})
	fmt.Println(string(b))
}

func main() {
	ctx := context.Background()
	cfg, err := config.LoadDefaultConfig(ctx)
	if err != nil {
		panic(err)
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	ssmClient := ssm.NewFromConfig(cfg)
	switchName := must("KILL_SWITCH_PARAM")

	if os.Getenv("HANDLER") == "killswitch" {
		lambda.Start(func(ctx context.Context, ev events.SNSEvent) error {
			for _, r := range ev.Records {
				log.Warn("usage alarm fired; pausing the assistant", "subject", r.SNS.Subject)
			}
			return killswitch.Set(ctx, ssmClient, switchName, false)
		})
		return
	}

	modelID := os.Getenv("MODEL_ID")
	if modelID == "" {
		modelID = propose.DefaultModelID
	}
	srv := &api.Server{
		Store:            &store.Dynamo{DB: dynamodb.NewFromConfig(cfg), KeysTable: must("KEYS_TABLE"), AuditTable: must("AUDIT_TABLE")},
		Model:            &propose.Bedrock{Client: bedrockruntime.NewFromConfig(cfg), ModelID: modelID},
		Switch:           &killswitch.SSM{Client: ssmClient, Name: switchName, TTL: 30 * time.Second},
		Log:              log,
		GlobalDailyLimit: envInt("GLOBAL_DAILY_LIMIT", 300),
		AuditRetention:   time.Duration(envInt("AUDIT_RETENTION_DAYS", 180)) * 24 * time.Hour,
		ModelTimeout:     time.Duration(envInt("MODEL_TIMEOUT_SECONDS", 20)) * time.Second,
		Metric:           emf,
	}
	lambda.Start(api.LambdaAdapter(srv.Handler()))
}
