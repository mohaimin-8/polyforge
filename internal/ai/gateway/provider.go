// Package gateway is the AI workload gateway (roadmap W18): one entry point
// that proxies chat completions to any OpenAI-compatible provider, streams
// tokens, and fronts the provider with a per-tenant semantic cache.
package gateway

import (
	"context"
	"encoding/json"
)

// Message is one chat turn in the OpenAI wire shape, which every provider
// the platform targets (OpenAI, Ollama, vLLM) speaks natively.
type Message struct {
	Role       string     `json:"role"`
	Content    string     `json:"content"`
	ToolCalls  []ToolCall `json:"tool_calls,omitempty"`
	ToolCallID string     `json:"tool_call_id,omitempty"`
	Name       string     `json:"name,omitempty"`
}

// ToolCall is the model asking the caller to run one tool.
type ToolCall struct {
	ID        string          `json:"id"`
	Name      string          `json:"name"`
	Arguments json.RawMessage `json:"arguments"`
}

// ToolSpec advertises a callable tool to the model as a JSON schema.
type ToolSpec struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	Schema      json.RawMessage `json:"schema"`
}

type ChatRequest struct {
	Model    string     `json:"model,omitempty"`
	Messages []Message  `json:"messages"`
	Tools    []ToolSpec `json:"tools,omitempty"`
}

type ChatResponse struct {
	Model            string  `json:"model"`
	Message          Message `json:"message"`
	FinishReason     string  `json:"finish_reason"`
	PromptTokens     int     `json:"prompt_tokens"`
	CompletionTokens int     `json:"completion_tokens"`
}

// Provider is the LLM behind the gateway. StreamChat invokes onDelta for
// each content fragment as it arrives and still returns the assembled
// response, so callers get identical semantics with and without streaming.
type Provider interface {
	Chat(ctx context.Context, req ChatRequest) (ChatResponse, error)
	StreamChat(ctx context.Context, req ChatRequest, onDelta func(string) error) (ChatResponse, error)
}
