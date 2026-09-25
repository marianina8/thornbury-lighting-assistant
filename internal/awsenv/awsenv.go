// Package awsenv loads AWS config for the CLIs and resolves the deployed
// stack's outputs, so issue-key and tla-admin need no hand-copied table names.
package awsenv

import (
	"context"
	"fmt"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/cloudformation"
)

// Defaults for this repo (the same values the Makefile passes to sam deploy).
const (
	DefaultStack   = "thornbury-lighting-assistant"
	DefaultProfile = "" // empty: the standard chain (AWS_PROFILE, SSO, env vars)
	DefaultRegion  = "us-west-2"
)

// Load returns an AWS config for the given profile and region.
func Load(ctx context.Context, profile, region string) (aws.Config, error) {
	opts := []func(*config.LoadOptions) error{config.WithRegion(region)}
	if profile != "" {
		opts = append(opts, config.WithSharedConfigProfile(profile))
	}
	return config.LoadDefaultConfig(ctx, opts...)
}

// StackOutputs returns a stack's outputs as a map.
func StackOutputs(ctx context.Context, cfg aws.Config, stack string) (map[string]string, error) {
	out, err := cloudformation.NewFromConfig(cfg).DescribeStacks(ctx, &cloudformation.DescribeStacksInput{StackName: aws.String(stack)})
	if err != nil {
		return nil, fmt.Errorf("describe stack %s: %w (has it been deployed? try `make sam-deploy`)", stack, err)
	}
	if len(out.Stacks) == 0 {
		return nil, fmt.Errorf("stack %s not found", stack)
	}
	m := map[string]string{}
	for _, o := range out.Stacks[0].Outputs {
		m[aws.ToString(o.OutputKey)] = aws.ToString(o.OutputValue)
	}
	return m, nil
}

// Require returns the named outputs or a clear error.
func Require(m map[string]string, names ...string) error {
	for _, n := range names {
		if m[n] == "" {
			return fmt.Errorf("stack output %s is missing", n)
		}
	}
	return nil
}
