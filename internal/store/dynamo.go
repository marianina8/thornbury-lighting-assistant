package store

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/feature/dynamodb/attributevalue"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
)

// DynamoAPI is the subset of the DynamoDB client we use.
type DynamoAPI interface {
	GetItem(context.Context, *dynamodb.GetItemInput, ...func(*dynamodb.Options)) (*dynamodb.GetItemOutput, error)
	PutItem(context.Context, *dynamodb.PutItemInput, ...func(*dynamodb.Options)) (*dynamodb.PutItemOutput, error)
	UpdateItem(context.Context, *dynamodb.UpdateItemInput, ...func(*dynamodb.Options)) (*dynamodb.UpdateItemOutput, error)
	Query(context.Context, *dynamodb.QueryInput, ...func(*dynamodb.Options)) (*dynamodb.QueryOutput, error)
	Scan(context.Context, *dynamodb.ScanInput, ...func(*dynamodb.Options)) (*dynamodb.ScanOutput, error)
}

// Dynamo stores keys and global counters in KeysTable (partition key "pk")
// and the audit trail in AuditTable (partition key "request_id", GSI
// "by_key" on key_id + created_at).
type Dynamo struct {
	DB         DynamoAPI
	KeysTable  string
	AuditTable string
}

// AuditByKeyIndex is the GSI name used by RecentAudit.
const AuditByKeyIndex = "by_key"

func keyPK(hash string) map[string]types.AttributeValue {
	return map[string]types.AttributeValue{"pk": &types.AttributeValueMemberS{Value: "key#" + hash}}
}

func s(v string) types.AttributeValue { return &types.AttributeValueMemberS{Value: v} }
func n(v int64) types.AttributeValue  { return &types.AttributeValueMemberN{Value: strconv.FormatInt(v, 10)} }

var trueAV = &types.AttributeValueMemberBOOL{Value: true}

func isCCF(err error) bool {
	var c *types.ConditionalCheckFailedException
	return errors.As(err, &c)
}

func (d *Dynamo) GetKey(ctx context.Context, hash string) (*Key, error) {
	out, err := d.DB.GetItem(ctx, &dynamodb.GetItemInput{TableName: &d.KeysTable, Key: keyPK(hash), ConsistentRead: aws.Bool(true)})
	if err != nil {
		return nil, err
	}
	if out.Item == nil {
		return nil, nil
	}
	var k Key
	if err := attributevalue.UnmarshalMap(out.Item, &k); err != nil {
		return nil, err
	}
	return &k, nil
}

func (d *Dynamo) Reserve(ctx context.Context, hash, period string, now time.Time) (Reservation, error) {
	ts := s(now.UTC().Format(time.RFC3339))
	// Same month, under the limit: count it.
	_, err := d.DB.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName:           &d.KeysTable,
		Key:                 keyPK(hash),
		UpdateExpression:    aws.String("SET used = used + :one, last_used_at = :ts"),
		ConditionExpression: aws.String("attribute_exists(pk) AND active = :t AND period = :p AND used < monthly_limit"),
		ExpressionAttributeValues: map[string]types.AttributeValue{
			":one": n(1), ":ts": ts, ":t": trueAV, ":p": s(period),
		},
	})
	if err == nil {
		return Reserved, nil
	}
	if !isCCF(err) {
		return 0, err
	}
	// First call of a new month: reset the counter to 1.
	_, err = d.DB.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName:           &d.KeysTable,
		Key:                 keyPK(hash),
		UpdateExpression:    aws.String("SET used = :one, period = :p, last_used_at = :ts"),
		ConditionExpression: aws.String("attribute_exists(pk) AND active = :t AND period <> :p AND monthly_limit > :zero"),
		ExpressionAttributeValues: map[string]types.AttributeValue{
			":one": n(1), ":zero": n(0), ":ts": ts, ":t": trueAV, ":p": s(period),
		},
	})
	if err == nil {
		return Reserved, nil
	}
	if !isCCF(err) {
		return 0, err
	}
	k, err := d.GetKey(ctx, hash)
	switch {
	case err != nil:
		return 0, err
	case k == nil:
		return NoSuchKey, nil
	case !k.Active:
		return Revoked, nil
	default:
		return QuotaExceeded, nil
	}
}

func (d *Dynamo) Refund(ctx context.Context, hash, period string) error {
	_, err := d.DB.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName:                 &d.KeysTable,
		Key:                       keyPK(hash),
		UpdateExpression:          aws.String("SET used = used - :one"),
		ConditionExpression:       aws.String("attribute_exists(pk) AND period = :p AND used > :zero"),
		ExpressionAttributeValues: map[string]types.AttributeValue{":one": n(1), ":zero": n(0), ":p": s(period)},
	})
	if isCCF(err) {
		return nil
	}
	return err
}

func (d *Dynamo) ReserveGlobal(ctx context.Context, day string, limit int, expires time.Time) (bool, error) {
	_, err := d.DB.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName:                 &d.KeysTable,
		Key:                       map[string]types.AttributeValue{"pk": s("global#" + day)},
		UpdateExpression:          aws.String("ADD calls :one SET expires_at = :exp"),
		ConditionExpression:       aws.String("attribute_not_exists(calls) OR calls < :limit"),
		ExpressionAttributeValues: map[string]types.AttributeValue{":one": n(1), ":limit": n(int64(limit)), ":exp": n(expires.Unix())},
	})
	if isCCF(err) {
		return false, nil
	}
	return err == nil, err
}

func (d *Dynamo) RefundGlobal(ctx context.Context, day string) error {
	_, err := d.DB.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName:                 &d.KeysTable,
		Key:                       map[string]types.AttributeValue{"pk": s("global#" + day)},
		UpdateExpression:          aws.String("ADD calls :minus"),
		ConditionExpression:       aws.String("calls > :zero"),
		ExpressionAttributeValues: map[string]types.AttributeValue{":minus": n(-1), ":zero": n(0)},
	})
	if isCCF(err) {
		return nil
	}
	return err
}

func (d *Dynamo) PutAudit(ctx context.Context, a Audit) error {
	item, err := attributevalue.MarshalMap(a)
	if err != nil {
		return err
	}
	_, err = d.DB.PutItem(ctx, &dynamodb.PutItemInput{TableName: &d.AuditTable, Item: item,
		ConditionExpression: aws.String("attribute_not_exists(request_id)")})
	return err
}

func (d *Dynamo) GetAudit(ctx context.Context, id string) (*Audit, error) {
	out, err := d.DB.GetItem(ctx, &dynamodb.GetItemInput{TableName: &d.AuditTable,
		Key: map[string]types.AttributeValue{"request_id": s(id)}, ConsistentRead: aws.Bool(true)})
	if err != nil || out.Item == nil {
		return nil, err
	}
	var a Audit
	return &a, attributevalue.UnmarshalMap(out.Item, &a)
}

func (d *Dynamo) SetOutcome(ctx context.Context, requestID, keyID, outcome, appliedJSON string, at time.Time) error {
	vals := map[string]types.AttributeValue{
		":o": s(outcome), ":at": s(at.UTC().Format(time.RFC3339Nano)), ":k": s(keyID), ":proposed": s(StatusProposed),
	}
	update := "SET outcome = :o, outcome_at = :at"
	if appliedJSON != "" {
		update += ", applied_values = :v"
		vals[":v"] = s(appliedJSON)
	}
	_, err := d.DB.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName:                 &d.AuditTable,
		Key:                       map[string]types.AttributeValue{"request_id": s(requestID)},
		UpdateExpression:          aws.String(update),
		ConditionExpression:       aws.String("attribute_exists(request_id) AND key_id = :k AND attribute_not_exists(outcome) AND #st = :proposed"),
		ExpressionAttributeNames:  map[string]string{"#st": "status"},
		ExpressionAttributeValues: vals,
	})
	if err == nil {
		return nil
	}
	if !isCCF(err) {
		return err
	}
	a, gerr := d.GetAudit(ctx, requestID)
	if gerr != nil {
		return gerr
	}
	if a == nil || a.KeyID != keyID {
		return ErrNotFound
	}
	return ErrConflict
}

func (d *Dynamo) PutKey(ctx context.Context, k Key) error {
	item, err := attributevalue.MarshalMap(k)
	if err != nil {
		return err
	}
	item["pk"] = s("key#" + k.Hash)
	_, err = d.DB.PutItem(ctx, &dynamodb.PutItemInput{TableName: &d.KeysTable, Item: item,
		ConditionExpression: aws.String("attribute_not_exists(pk)")})
	if isCCF(err) {
		return ErrExists
	}
	return err
}

func (d *Dynamo) ListKeys(ctx context.Context) ([]Key, error) {
	var out []Key
	var start map[string]types.AttributeValue
	for {
		page, err := d.DB.Scan(ctx, &dynamodb.ScanInput{
			TableName:                 &d.KeysTable,
			FilterExpression:          aws.String("begins_with(pk, :k)"),
			ExpressionAttributeValues: map[string]types.AttributeValue{":k": s("key#")},
			ExclusiveStartKey:         start,
		})
		if err != nil {
			return nil, err
		}
		for _, it := range page.Items {
			var k Key
			if err := attributevalue.UnmarshalMap(it, &k); err != nil {
				return nil, err
			}
			out = append(out, k)
		}
		if page.LastEvaluatedKey == nil {
			break
		}
		start = page.LastEvaluatedKey
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt < out[j].CreatedAt })
	return out, nil
}

func (d *Dynamo) SetKeyLimit(ctx context.Context, hash string, limit int) error {
	_, err := d.DB.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName: &d.KeysTable, Key: keyPK(hash),
		UpdateExpression:          aws.String("SET monthly_limit = :l"),
		ConditionExpression:       aws.String("attribute_exists(pk)"),
		ExpressionAttributeValues: map[string]types.AttributeValue{":l": n(int64(limit))},
	})
	if isCCF(err) {
		return ErrNotFound
	}
	return err
}

func (d *Dynamo) RevokeKey(ctx context.Context, hash string, at time.Time) error {
	_, err := d.DB.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName: &d.KeysTable, Key: keyPK(hash),
		UpdateExpression:          aws.String("SET active = :f, revoked_at = :at"),
		ConditionExpression:       aws.String("attribute_exists(pk)"),
		ExpressionAttributeValues: map[string]types.AttributeValue{":f": &types.AttributeValueMemberBOOL{Value: false}, ":at": s(at.UTC().Format(time.RFC3339))},
	})
	if isCCF(err) {
		return ErrNotFound
	}
	return err
}

func (d *Dynamo) RecentAudit(ctx context.Context, keyID string, limit int) ([]Audit, error) {
	var items []map[string]types.AttributeValue
	if keyID != "" {
		out, err := d.DB.Query(ctx, &dynamodb.QueryInput{
			TableName: &d.AuditTable, IndexName: aws.String(AuditByKeyIndex),
			KeyConditionExpression:    aws.String("key_id = :k"),
			ExpressionAttributeValues: map[string]types.AttributeValue{":k": s(keyID)},
			ScanIndexForward:          aws.Bool(false),
			Limit:                     aws.Int32(int32(limit)),
		})
		if err != nil {
			return nil, err
		}
		items = out.Items
	} else {
		var start map[string]types.AttributeValue
		for {
			out, err := d.DB.Scan(ctx, &dynamodb.ScanInput{TableName: &d.AuditTable, ExclusiveStartKey: start})
			if err != nil {
				return nil, err
			}
			items = append(items, out.Items...)
			if out.LastEvaluatedKey == nil {
				break
			}
			start = out.LastEvaluatedKey
		}
	}
	res := make([]Audit, 0, len(items))
	for _, it := range items {
		var a Audit
		if err := attributevalue.UnmarshalMap(it, &a); err != nil {
			return nil, fmt.Errorf("audit item: %w", err)
		}
		res = append(res, a)
	}
	sort.Slice(res, func(i, j int) bool { return res[i].CreatedAt > res[j].CreatedAt })
	if len(res) > limit {
		res = res[:limit]
	}
	return res, nil
}
