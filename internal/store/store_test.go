package store

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
)

var t0 = time.Date(2026, 9, 24, 12, 0, 0, 0, time.UTC)

func newKey(hash, label string, limit int) Key {
	return Key{Hash: hash, ID: hash[:4], Prefix: "tla_x", Label: label, MonthlyLimit: limit, Active: true,
		Period: Period(t0), CreatedAt: t0.Format(time.RFC3339)}
}

// suite is the contract both stores must meet.
func suite(t *testing.T, mk func(t *testing.T) Store) {
	ctx := context.Background()

	t.Run("quota counts to the limit then refuses", func(t *testing.T) {
		s := mk(t)
		if err := s.PutKey(ctx, newKey("aaaa1", "tester", 3)); err != nil {
			t.Fatal(err)
		}
		if err := s.PutKey(ctx, newKey("aaaa1", "dup", 3)); !errors.Is(err, ErrExists) {
			t.Fatalf("duplicate key: %v", err)
		}
		for i := 0; i < 3; i++ {
			if r, err := s.Reserve(ctx, "aaaa1", Period(t0), t0); err != nil || r != Reserved {
				t.Fatalf("call %d: %v %v", i, r, err)
			}
		}
		if r, _ := s.Reserve(ctx, "aaaa1", Period(t0), t0); r != QuotaExceeded {
			t.Fatalf("4th call: %v", r)
		}
		// Refund frees one slot.
		if err := s.Refund(ctx, "aaaa1", Period(t0)); err != nil {
			t.Fatal(err)
		}
		if r, _ := s.Reserve(ctx, "aaaa1", Period(t0), t0); r != Reserved {
			t.Fatalf("after refund: %v", r)
		}
		// A new month resets the counter.
		next := t0.AddDate(0, 1, 0)
		if r, _ := s.Reserve(ctx, "aaaa1", Period(next), next); r != Reserved {
			t.Fatalf("new month: %v", r)
		}
		k, _ := s.GetKey(ctx, "aaaa1")
		if k.Used != 1 || k.Period != Period(next) {
			t.Fatalf("after reset: %+v", k)
		}
	})

	t.Run("unknown and revoked keys", func(t *testing.T) {
		s := mk(t)
		if r, _ := s.Reserve(ctx, "nope", Period(t0), t0); r != NoSuchKey {
			t.Fatalf("unknown: %v", r)
		}
		_ = s.PutKey(ctx, newKey("bbbb1", "t", 5))
		if err := s.RevokeKey(ctx, "bbbb1", t0); err != nil {
			t.Fatal(err)
		}
		if r, _ := s.Reserve(ctx, "bbbb1", Period(t0), t0); r != Revoked {
			t.Fatalf("revoked: %v", r)
		}
		if r, _ := s.Reserve(ctx, "bbbb1", Period(t0.AddDate(0, 1, 0)), t0); r != Revoked {
			t.Fatalf("revoked key must not reset in a new month: %v", r)
		}
		if err := s.RevokeKey(ctx, "missing", t0); !errors.Is(err, ErrNotFound) {
			t.Fatal("revoke missing")
		}
	})

	t.Run("zero limit means no calls", func(t *testing.T) {
		s := mk(t)
		k := newKey("cccc1", "paused", 0)
		k.Period = "2026-08"
		_ = s.PutKey(ctx, k)
		if r, _ := s.Reserve(ctx, "cccc1", Period(t0), t0); r != QuotaExceeded {
			t.Fatalf("zero limit: %v", r)
		}
	})

	t.Run("concurrent reservations never exceed the limit", func(t *testing.T) {
		s := mk(t)
		_ = s.PutKey(ctx, newKey("dddd1", "race", 10))
		var ok int64
		var wg sync.WaitGroup
		for i := 0; i < 30; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				if r, err := s.Reserve(ctx, "dddd1", Period(t0), t0); err == nil && r == Reserved {
					atomic.AddInt64(&ok, 1)
				}
			}()
		}
		wg.Wait()
		if ok != 10 {
			t.Fatalf("reserved %d, want exactly 10", ok)
		}
	})

	t.Run("global daily cap", func(t *testing.T) {
		s := mk(t)
		for i := 0; i < 2; i++ {
			if ok, err := s.ReserveGlobal(ctx, "2026-09-24", 2, t0); !ok || err != nil {
				t.Fatalf("global %d: %v %v", i, ok, err)
			}
		}
		if ok, _ := s.ReserveGlobal(ctx, "2026-09-24", 2, t0); ok {
			t.Fatal("global cap exceeded")
		}
		_ = s.RefundGlobal(ctx, "2026-09-24")
		if ok, _ := s.ReserveGlobal(ctx, "2026-09-24", 2, t0); !ok {
			t.Fatal("refund did not free a slot")
		}
		if ok, _ := s.ReserveGlobal(ctx, "2026-09-25", 2, t0); !ok {
			t.Fatal("new day should start fresh")
		}
		// Regression: a limit of 0 must allow nothing (Dynamo used to allow the first call).
		if ok, _ := s.ReserveGlobal(ctx, "2026-09-26", 0, t0); ok {
			t.Fatal("limit 0 must refuse")
		}
	})

	t.Run("outcome once, by the owner, only for proposals", func(t *testing.T) {
		s := mk(t)
		a := Audit{RequestID: "r1", KeyID: "k1", CreatedAt: t0.Format(time.RFC3339Nano), Status: StatusProposed, Current: "{}", ModelID: "m"}
		if err := s.PutAudit(ctx, a); err != nil {
			t.Fatal(err)
		}
		if err := s.SetOutcome(ctx, "r1", "someone-else", "applied", "", t0); !errors.Is(err, ErrNotFound) {
			t.Fatalf("other key: %v", err)
		}
		if err := s.SetOutcome(ctx, "missing", "k1", "applied", "", t0); !errors.Is(err, ErrNotFound) {
			t.Fatalf("missing: %v", err)
		}
		if err := s.SetOutcome(ctx, "r1", "k1", "edited", `{"energy":500}`, t0); err != nil {
			t.Fatal(err)
		}
		if err := s.SetOutcome(ctx, "r1", "k1", "discarded", "", t0); !errors.Is(err, ErrConflict) {
			t.Fatalf("second outcome: %v", err)
		}
		got, _ := s.GetAudit(ctx, "r1")
		if got.Outcome != "edited" || got.AppliedValues != `{"energy":500}` {
			t.Fatalf("stored: %+v", got)
		}
		b := Audit{RequestID: "r2", KeyID: "k1", CreatedAt: t0.Add(time.Second).Format(time.RFC3339Nano), Status: StatusOutOfScope, Current: "{}", ModelID: "m"}
		_ = s.PutAudit(ctx, b)
		if err := s.SetOutcome(ctx, "r2", "k1", "applied", "", t0); !errors.Is(err, ErrConflict) {
			t.Fatalf("outcome on a non-proposal: %v", err)
		}
		recent, err := s.RecentAudit(ctx, "k1", 10)
		if err != nil || len(recent) != 2 || recent[0].RequestID != "r2" {
			t.Fatalf("recent: %+v %v", recent, err)
		}
	})

	t.Run("admin list and limit", func(t *testing.T) {
		s := mk(t)
		_ = s.PutKey(ctx, newKey("eeee1", "a", 5))
		if err := s.SetKeyLimit(ctx, "eeee1", 99); err != nil {
			t.Fatal(err)
		}
		ks, _ := s.ListKeys(ctx)
		if len(ks) != 1 || ks[0].MonthlyLimit != 99 || ks[0].Hash != "eeee1" {
			t.Fatalf("list: %+v", ks)
		}
		if err := s.SetKeyLimit(ctx, "missing", 1); !errors.Is(err, ErrNotFound) {
			t.Fatal("limit on missing key")
		}
	})
}

func TestMemoryStore(t *testing.T) {
	suite(t, func(*testing.T) Store { return NewMemory() })
}

// TestDynamoStore runs the same contract against a DynamoDB-compatible
// endpoint (e.g. `moto_server` or DynamoDB Local) when DYNAMO_TEST_ENDPOINT is set.
func TestDynamoStore(t *testing.T) {
	ep := os.Getenv("DYNAMO_TEST_ENDPOINT")
	if ep == "" {
		t.Skip("set DYNAMO_TEST_ENDPOINT to run against a local DynamoDB")
	}
	client := dynamodb.New(dynamodb.Options{
		Region: "us-west-2", BaseEndpoint: aws.String(ep),
		Credentials: credentials.NewStaticCredentialsProvider("test", "test", ""),
	})
	i := 0
	suite(t, func(t *testing.T) Store {
		i++
		d := &Dynamo{DB: client, KeysTable: fmt.Sprintf("keys-%d-%d", time.Now().UnixNano(), i), AuditTable: fmt.Sprintf("audit-%d-%d", time.Now().UnixNano(), i)}
		createTables(t, client, d)
		return d
	})
}

func createTables(t *testing.T, c *dynamodb.Client, d *Dynamo) {
	ctx := context.Background()
	_, err := c.CreateTable(ctx, &dynamodb.CreateTableInput{
		TableName: &d.KeysTable, BillingMode: types.BillingModePayPerRequest,
		AttributeDefinitions: []types.AttributeDefinition{{AttributeName: aws.String("pk"), AttributeType: types.ScalarAttributeTypeS}},
		KeySchema:            []types.KeySchemaElement{{AttributeName: aws.String("pk"), KeyType: types.KeyTypeHash}},
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = c.CreateTable(ctx, &dynamodb.CreateTableInput{
		TableName: &d.AuditTable, BillingMode: types.BillingModePayPerRequest,
		AttributeDefinitions: []types.AttributeDefinition{
			{AttributeName: aws.String("request_id"), AttributeType: types.ScalarAttributeTypeS},
			{AttributeName: aws.String("key_id"), AttributeType: types.ScalarAttributeTypeS},
			{AttributeName: aws.String("created_at"), AttributeType: types.ScalarAttributeTypeS},
		},
		KeySchema: []types.KeySchemaElement{{AttributeName: aws.String("request_id"), KeyType: types.KeyTypeHash}},
		GlobalSecondaryIndexes: []types.GlobalSecondaryIndex{{
			IndexName: aws.String(AuditByKeyIndex),
			KeySchema: []types.KeySchemaElement{
				{AttributeName: aws.String("key_id"), KeyType: types.KeyTypeHash},
				{AttributeName: aws.String("created_at"), KeyType: types.KeyTypeRange},
			},
			Projection: &types.Projection{ProjectionType: types.ProjectionTypeAll},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
}
