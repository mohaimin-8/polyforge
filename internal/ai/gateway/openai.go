package gateway

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// OpenAICompat speaks the OpenAI chat-completions wire protocol, which is
// the de-facto standard also exposed by Ollama and vLLM. Switching model
// hosts is a base-URL change, not a code change.
type OpenAICompat struct {
	baseURL string
	apiKey  string
	model   string
	client  *http.Client
}

func NewOpenAICompat(baseURL, apiKey, defaultModel string) *OpenAICompat {
	return &OpenAICompat{
		baseURL: strings.TrimRight(baseURL, "/"),
		apiKey:  apiKey,
		model:   defaultModel,
		client:  egressClient(120 * time.Second),
	}
}

// Wire types for the OpenAI protocol; the gateway's own types stay
// provider-neutral.
type oaiToolCall struct {
	ID       string `json:"id"`
	Type     string `json:"type"`
	Function struct {
		Name      string `json:"name"`
		Arguments string `json:"arguments"`
	} `json:"function"`
}

type oaiMessage struct {
	Role       string        `json:"role"`
	Content    string        `json:"content"`
	ToolCalls  []oaiToolCall `json:"tool_calls,omitempty"`
	ToolCallID string        `json:"tool_call_id,omitempty"`
	Name       string        `json:"name,omitempty"`
}

func (p *OpenAICompat) buildBody(req ChatRequest, stream bool) ([]byte, error) {
	model := req.Model
	if model == "" {
		model = p.model
	}
	messages := make([]oaiMessage, len(req.Messages))
	for i, m := range req.Messages {
		om := oaiMessage{Role: m.Role, Content: m.Content, ToolCallID: m.ToolCallID, Name: m.Name}
		for _, tc := range m.ToolCalls {
			var otc oaiToolCall
			otc.ID = tc.ID
			otc.Type = "function"
			otc.Function.Name = tc.Name
			otc.Function.Arguments = string(tc.Arguments)
			om.ToolCalls = append(om.ToolCalls, otc)
		}
		messages[i] = om
	}
	body := map[string]any{"model": model, "messages": messages, "stream": stream}
	if len(req.Tools) > 0 {
		tools := make([]map[string]any, len(req.Tools))
		for i, t := range req.Tools {
			tools[i] = map[string]any{
				"type": "function",
				"function": map[string]any{
					"name":        t.Name,
					"description": t.Description,
					"parameters":  json.RawMessage(t.Schema),
				},
			}
		}
		body["tools"] = tools
	}
	return json.Marshal(body)
}

func (p *OpenAICompat) post(ctx context.Context, body []byte) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, p.baseURL+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+p.apiKey)
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		_ = resp.Body.Close()
		return nil, fmt.Errorf("chat API returned %d: %s", resp.StatusCode, detail)
	}
	return resp, nil
}

func (p *OpenAICompat) Chat(ctx context.Context, req ChatRequest) (ChatResponse, error) {
	body, err := p.buildBody(req, false)
	if err != nil {
		return ChatResponse{}, err
	}
	resp, err := p.post(ctx, body)
	if err != nil {
		return ChatResponse{}, err
	}
	defer func() { _ = resp.Body.Close() }()

	var parsed struct {
		Model   string `json:"model"`
		Choices []struct {
			Message      oaiMessage `json:"message"`
			FinishReason string     `json:"finish_reason"`
		} `json:"choices"`
		Usage struct {
			PromptTokens     int `json:"prompt_tokens"`
			CompletionTokens int `json:"completion_tokens"`
		} `json:"usage"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		return ChatResponse{}, err
	}
	if len(parsed.Choices) == 0 {
		return ChatResponse{}, fmt.Errorf("chat API returned no choices")
	}
	choice := parsed.Choices[0]
	out := ChatResponse{
		Model:            parsed.Model,
		FinishReason:     choice.FinishReason,
		PromptTokens:     parsed.Usage.PromptTokens,
		CompletionTokens: parsed.Usage.CompletionTokens,
		Message:          Message{Role: choice.Message.Role, Content: choice.Message.Content},
	}
	for _, tc := range choice.Message.ToolCalls {
		out.Message.ToolCalls = append(out.Message.ToolCalls, ToolCall{
			ID:        tc.ID,
			Name:      tc.Function.Name,
			Arguments: json.RawMessage(tc.Function.Arguments),
		})
	}
	return out, nil
}

// StreamChat consumes the provider's SSE stream, forwarding content deltas
// as they arrive and assembling the final response.
func (p *OpenAICompat) StreamChat(ctx context.Context, req ChatRequest, onDelta func(string) error) (ChatResponse, error) {
	body, err := p.buildBody(req, true)
	if err != nil {
		return ChatResponse{}, err
	}
	resp, err := p.post(ctx, body)
	if err != nil {
		return ChatResponse{}, err
	}
	defer func() { _ = resp.Body.Close() }()

	var out ChatResponse
	var content strings.Builder
	out.Message.Role = "assistant"

	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if payload == "[DONE]" {
			break
		}
		var chunk struct {
			Model   string `json:"model"`
			Choices []struct {
				Delta struct {
					Content string `json:"content"`
				} `json:"delta"`
				FinishReason string `json:"finish_reason"`
			} `json:"choices"`
		}
		if err := json.Unmarshal([]byte(payload), &chunk); err != nil {
			return ChatResponse{}, fmt.Errorf("decode stream chunk: %w", err)
		}
		if chunk.Model != "" {
			out.Model = chunk.Model
		}
		for _, choice := range chunk.Choices {
			if choice.Delta.Content != "" {
				content.WriteString(choice.Delta.Content)
				if onDelta != nil {
					if err := onDelta(choice.Delta.Content); err != nil {
						return ChatResponse{}, err
					}
				}
			}
			if choice.FinishReason != "" {
				out.FinishReason = choice.FinishReason
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return ChatResponse{}, err
	}
	out.Message.Content = content.String()
	return out, nil
}
