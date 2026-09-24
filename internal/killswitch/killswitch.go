// Package killswitch is the global off switch: an SSM parameter holding
// "true" or "false". It is cached briefly and fails closed, so an SSM outage
// pauses the assistant rather than leaving it unguarded.
package killswitch

import (
	"context"
	"strings"
	"sync"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/ssm"
	ssmtypes "github.com/aws/aws-sdk-go-v2/service/ssm/types"
)

// Switch reports whether the assistant may make model calls.
type Switch interface {
	Enabled(ctx context.Context) bool
}

// Static is a fixed switch for tests and local runs.
type Static bool

func (s Static) Enabled(context.Context) bool { return bool(s) }

// SSMAPI is the subset of the SSM client we use.
type SSMAPI interface {
	GetParameter(context.Context, *ssm.GetParameterInput, ...func(*ssm.Options)) (*ssm.GetParameterOutput, error)
	PutParameter(context.Context, *ssm.PutParameterInput, ...func(*ssm.Options)) (*ssm.PutParameterOutput, error)
}

// SSM reads the switch from Parameter Store.
type SSM struct {
	Client SSMAPI
	Name   string
	TTL    time.Duration
	Now    func() time.Time

	mu      sync.Mutex
	value   bool
	fetched time.Time
}

func (s *SSM) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

func (s *SSM) Enabled(ctx context.Context) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.fetched.IsZero() && s.now().Sub(s.fetched) < s.TTL {
		return s.value
	}
	out, err := s.Client.GetParameter(ctx, &ssm.GetParameterInput{Name: aws.String(s.Name)})
	v := false // fail closed
	if err == nil && out.Parameter != nil {
		v = strings.EqualFold(strings.TrimSpace(aws.ToString(out.Parameter.Value)), "true")
	}
	s.value, s.fetched = v, s.now()
	return v
}

// Set writes the switch (used by the admin CLI and the alarm handler).
func Set(ctx context.Context, c SSMAPI, name string, enabled bool) error {
	v := "false"
	if enabled {
		v = "true"
	}
	_, err := c.PutParameter(ctx, &ssm.PutParameterInput{
		Name: aws.String(name), Value: aws.String(v), Overwrite: aws.Bool(true), Type: ssmtypes.ParameterTypeString,
	})
	return err
}
