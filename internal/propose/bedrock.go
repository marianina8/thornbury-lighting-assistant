package propose

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime"
	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime/document"
	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime/types"
)

// DefaultModelID is the Claude Haiku 4.5 US cross-region inference profile.
const DefaultModelID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

// MaxOutputTokens bounds the response size (and so the cost) of every call.
const MaxOutputTokens = 400

// ConverseAPI is the one Bedrock operation we use (an interface for tests).
type ConverseAPI interface {
	Converse(ctx context.Context, in *bedrockruntime.ConverseInput, opts ...func(*bedrockruntime.Options)) (*bedrockruntime.ConverseOutput, error)
}

// Bedrock makes one Converse call with a forced tool so the answer is always
// structured JSON. Text-only, no images, temperature 0.
type Bedrock struct {
	Client  ConverseAPI
	ModelID string
}

func (b *Bedrock) ID() string { return b.ModelID }

func (b *Bedrock) Propose(ctx context.Context, in Input) (Output, Usage, error) {
	out, err := b.Client.Converse(ctx, &bedrockruntime.ConverseInput{
		ModelId: aws.String(b.ModelID),
		System:  []types.SystemContentBlock{&types.SystemContentBlockMemberText{Value: SystemPrompt}},
		Messages: []types.Message{{
			Role:    types.ConversationRoleUser,
			Content: []types.ContentBlock{&types.ContentBlockMemberText{Value: UserMessage(in)}},
		}},
		InferenceConfig: &types.InferenceConfiguration{MaxTokens: aws.Int32(MaxOutputTokens), Temperature: aws.Float32(0)},
		ToolConfig: &types.ToolConfiguration{
			Tools: []types.Tool{&types.ToolMemberToolSpec{Value: types.ToolSpecification{
				Name:        aws.String(ToolName),
				Description: aws.String("Propose new settings for the artist's spot light."),
				InputSchema: &types.ToolInputSchemaMemberJson{Value: document.NewLazyDocument(ToolSchema())},
			}}},
			ToolChoice: &types.ToolChoiceMemberTool{Value: types.SpecificToolChoice{Name: aws.String(ToolName)}},
		},
	})
	if err != nil {
		return Output{}, Usage{}, fmt.Errorf("bedrock converse: %w", err)
	}
	var u Usage
	if out.Usage != nil {
		u.InputTokens = int(aws.ToInt32(out.Usage.InputTokens))
		u.OutputTokens = int(aws.ToInt32(out.Usage.OutputTokens))
	}
	msg, ok := out.Output.(*types.ConverseOutputMemberMessage)
	if !ok {
		return Output{}, u, errors.New("bedrock: no message in response")
	}
	for _, c := range msg.Value.Content {
		if tu, ok := c.(*types.ContentBlockMemberToolUse); ok && aws.ToString(tu.Value.Name) == ToolName {
			if tu.Value.Input == nil {
				return Output{}, u, errors.New("bedrock: empty tool input")
			}
			raw, err := tu.Value.Input.MarshalSmithyDocument()
			if err != nil {
				return Output{}, u, fmt.Errorf("bedrock: tool input: %w", err)
			}
			var o Output
			if err := json.Unmarshal(raw, &o); err != nil {
				return Output{}, u, fmt.Errorf("bedrock: tool input: %w", err)
			}
			return o, u, nil
		}
	}
	return Output{}, u, fmt.Errorf("bedrock: model did not call %s (stop reason %s)", ToolName, out.StopReason)
}
