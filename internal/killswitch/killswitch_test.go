package killswitch

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/ssm"
	ssmtypes "github.com/aws/aws-sdk-go-v2/service/ssm/types"
)

type fakeSSM struct {
	val   string
	err   error
	calls int
}

func (f *fakeSSM) GetParameter(context.Context, *ssm.GetParameterInput, ...func(*ssm.Options)) (*ssm.GetParameterOutput, error) {
	f.calls++
	if f.err != nil {
		return nil, f.err
	}
	return &ssm.GetParameterOutput{Parameter: &ssmtypes.Parameter{Value: aws.String(f.val)}}, nil
}

func (f *fakeSSM) PutParameter(_ context.Context, in *ssm.PutParameterInput, _ ...func(*ssm.Options)) (*ssm.PutParameterOutput, error) {
	f.val = aws.ToString(in.Value)
	return &ssm.PutParameterOutput{}, nil
}

func TestSSMSwitchCachesAndFailsClosed(t *testing.T) {
	now := time.Unix(0, 0)
	f := &fakeSSM{val: "true"}
	s := &SSM{Client: f, Name: "/x", TTL: 30 * time.Second, Now: func() time.Time { return now }}
	if !s.Enabled(context.Background()) {
		t.Fatal("should be enabled")
	}
	_ = Set(context.Background(), f, "/x", false)
	if !s.Enabled(context.Background()) || f.calls != 1 {
		t.Fatal("should be served from cache")
	}
	now = now.Add(31 * time.Second)
	if s.Enabled(context.Background()) {
		t.Fatal("should pick up the switch after the TTL")
	}
	f.err = errors.New("ssm down")
	now = now.Add(31 * time.Second)
	_ = Set(context.Background(), f, "/x", true)
	if s.Enabled(context.Background()) {
		t.Fatal("must fail closed when SSM errors")
	}
	f.err, f.val = nil, "yes please"
	now = now.Add(31 * time.Second)
	if s.Enabled(context.Background()) {
		t.Fatal("only the exact value true enables it")
	}
}
