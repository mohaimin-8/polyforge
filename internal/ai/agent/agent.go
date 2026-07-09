// Package agent implements the tool-use loop (roadmap W19): the model
// returns structured tool calls, the runner executes them, feeds results
// back, and repeats until the model produces a final answer. Every LLM call
// and every tool call is its own OTel span under one agent.run parent, so a
// single user request fans out into the multi-span causal chain that
// defines the agentic-multistep workload class.
package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
	"go.opentelemetry.io/otel/trace/noop"

	"polyforge/internal/ai/gateway"
)

const DefaultMaxSteps = 8

// Tool is one callable capability advertised to the model.
type Tool interface {
	Name() string
	Description() string
	// Schema is the JSON schema of the tool's arguments object.
	Schema() json.RawMessage
	// Call runs the tool. tenantID scopes every tool to the calling tenant;
	// a tool must never read another tenant's data.
	Call(ctx context.Context, tenantID string, args json.RawMessage) (string, error)
}

// StepTrace records one loop iteration for the response payload, mirroring
// what the OTel spans record for the trace backend.
type StepTrace struct {
	Kind   string `json:"kind"` // "llm" or "tool"
	Tool   string `json:"tool,omitempty"`
	Args   string `json:"args,omitempty"`
	Result string `json:"result,omitempty"`
}

type Result struct {
	Output    string      `json:"output"`
	Steps     []StepTrace `json:"steps"`
	LLMCalls  int         `json:"llm_calls"`
	ToolCalls int         `json:"tool_calls"`
}

type Runner struct {
	provider gateway.Provider
	tools    []Tool
	specs    []gateway.ToolSpec
	tracer   trace.Tracer
	maxSteps int
}

func NewRunner(provider gateway.Provider, tools []Tool, tracerProvider trace.TracerProvider, maxSteps int) *Runner {
	if maxSteps <= 0 {
		maxSteps = DefaultMaxSteps
	}
	if tracerProvider == nil {
		tracerProvider = noop.NewTracerProvider()
	}
	specs := make([]gateway.ToolSpec, len(tools))
	for i, tool := range tools {
		specs[i] = gateway.ToolSpec{Name: tool.Name(), Description: tool.Description(), Schema: tool.Schema()}
	}
	return &Runner{
		provider: provider,
		tools:    tools,
		specs:    specs,
		tracer:   tracerProvider.Tracer("polyforge/internal/ai/agent"),
		maxSteps: maxSteps,
	}
}

func (r *Runner) tool(name string) Tool {
	for _, tool := range r.tools {
		if tool.Name() == name {
			return tool
		}
	}
	return nil
}

// Run drives the loop for one prompt.
func (r *Runner) Run(ctx context.Context, tenantID, prompt string) (Result, error) {
	ctx, span := r.tracer.Start(ctx, "agent.run", trace.WithAttributes(
		attribute.String("polyforge.tenant_id", tenantID),
	))
	defer span.End()

	messages := []gateway.Message{
		{Role: "system", Content: "You are PolyForge's tenant assistant. Use the available tools when they help; answer directly when they do not."},
		{Role: "user", Content: prompt},
	}
	var result Result

	for step := 0; step < r.maxSteps; step++ {
		response, err := r.llmCall(ctx, messages)
		if err != nil {
			span.SetStatus(codes.Error, "llm call failed")
			span.RecordError(err)
			return result, err
		}
		result.LLMCalls++
		result.Steps = append(result.Steps, StepTrace{Kind: "llm"})

		if len(response.Message.ToolCalls) == 0 {
			result.Output = response.Message.Content
			span.SetAttributes(
				attribute.Int("agent.llm_calls", result.LLMCalls),
				attribute.Int("agent.tool_calls", result.ToolCalls),
			)
			return result, nil
		}

		messages = append(messages, response.Message)
		for _, call := range response.Message.ToolCalls {
			output := r.toolCall(ctx, tenantID, call)
			result.ToolCalls++
			result.Steps = append(result.Steps, StepTrace{
				Kind: "tool", Tool: call.Name, Args: string(call.Arguments), Result: output,
			})
			messages = append(messages, gateway.Message{
				Role:       "tool",
				Content:    output,
				ToolCallID: call.ID,
				Name:       call.Name,
			})
		}
	}
	err := fmt.Errorf("agent did not converge within %d steps", r.maxSteps)
	span.SetStatus(codes.Error, err.Error())
	return result, err
}

func (r *Runner) llmCall(ctx context.Context, messages []gateway.Message) (gateway.ChatResponse, error) {
	ctx, span := r.tracer.Start(ctx, "llm.call", trace.WithAttributes(
		attribute.Int("llm.messages", len(messages)),
	))
	defer span.End()
	response, err := r.provider.Chat(ctx, gateway.ChatRequest{Messages: messages, Tools: r.specs})
	if err != nil {
		span.SetStatus(codes.Error, err.Error())
		return gateway.ChatResponse{}, err
	}
	span.SetAttributes(attribute.Int("llm.tool_calls", len(response.Message.ToolCalls)))
	return response, nil
}

// toolCall executes one tool. Tool failures are reported back to the model
// as tool output rather than aborting the run: the model can recover by
// trying different arguments or answering without the tool.
func (r *Runner) toolCall(ctx context.Context, tenantID string, call gateway.ToolCall) string {
	ctx, span := r.tracer.Start(ctx, "tool.call", trace.WithAttributes(
		attribute.String("tool.name", call.Name),
	))
	defer span.End()

	tool := r.tool(call.Name)
	if tool == nil {
		err := errors.New("unknown tool: " + call.Name)
		span.SetStatus(codes.Error, err.Error())
		return "error: " + err.Error()
	}
	output, err := tool.Call(ctx, tenantID, call.Arguments)
	if err != nil {
		span.SetStatus(codes.Error, err.Error())
		return "error: " + err.Error()
	}
	return output
}

// GatewayFunc adapts the runner to the gateway's agent hook, returning the
// span fan-out (LLM + tool calls) so the gateway's telemetry reflects the
// agentic workload shape.
func (r *Runner) GatewayFunc() gateway.AgentFunc {
	return func(ctx context.Context, tenantID, prompt string) (any, int, error) {
		result, err := r.Run(ctx, tenantID, prompt)
		return result, result.LLMCalls + result.ToolCalls, err
	}
}
