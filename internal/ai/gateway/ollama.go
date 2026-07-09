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

// Ollama speaks Ollama's native /api/chat protocol (roadmap W20). The
// OpenAI-compatible shim at /v1 would also work, but the native API reports
// prompt_eval_count and eval_count, which the inference benchmark needs to
// compute tokens/sec without guessing at the tokenizer.
type Ollama struct {
	baseURL string
	model   string
	client  *http.Client
}

func NewOllama(baseURL, defaultModel string) *Ollama {
	return &Ollama{
		baseURL: strings.TrimRight(baseURL, "/"),
		model:   defaultModel,
		client:  &http.Client{Timeout: 300 * time.Second},
	}
}

// Wire types for the native Ollama protocol. Unlike OpenAI, tool-call
// arguments are a JSON object rather than a string-encoded object.
type ollamaToolCall struct {
	Function struct {
		Name      string          `json:"name"`
		Arguments json.RawMessage `json:"arguments"`
	} `json:"function"`
}

type ollamaMessage struct {
	Role      string           `json:"role"`
	Content   string           `json:"content"`
	ToolCalls []ollamaToolCall `json:"tool_calls,omitempty"`
}

type ollamaChunk struct {
	Model           string        `json:"model"`
	Message         ollamaMessage `json:"message"`
	Done            bool          `json:"done"`
	DoneReason      string        `json:"done_reason"`
	PromptEvalCount int           `json:"prompt_eval_count"`
	EvalCount       int           `json:"eval_count"`
}

func (p *Ollama) buildBody(req ChatRequest, stream bool) ([]byte, error) {
	model := req.Model
	if model == "" {
		model = p.model
	}
	messages := make([]ollamaMessage, len(req.Messages))
	for i, m := range req.Messages {
		om := ollamaMessage{Role: m.Role, Content: m.Content}
		for _, tc := range m.ToolCalls {
			var otc ollamaToolCall
			otc.Function.Name = tc.Name
			otc.Function.Arguments = tc.Arguments
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

func (p *Ollama) post(ctx context.Context, body []byte) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, p.baseURL+"/api/chat", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		_ = resp.Body.Close()
		return nil, fmt.Errorf("ollama returned %d: %s", resp.StatusCode, detail)
	}
	return resp, nil
}

func fromOllamaMessage(m ollamaMessage) Message {
	out := Message{Role: m.Role, Content: m.Content}
	for _, tc := range m.ToolCalls {
		out.ToolCalls = append(out.ToolCalls, ToolCall{
			Name:      tc.Function.Name,
			Arguments: tc.Function.Arguments,
		})
	}
	return out
}

func (p *Ollama) Chat(ctx context.Context, req ChatRequest) (ChatResponse, error) {
	body, err := p.buildBody(req, false)
	if err != nil {
		return ChatResponse{}, err
	}
	resp, err := p.post(ctx, body)
	if err != nil {
		return ChatResponse{}, err
	}
	defer func() { _ = resp.Body.Close() }()

	var parsed ollamaChunk
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		return ChatResponse{}, err
	}
	return ChatResponse{
		Model:            parsed.Model,
		Message:          fromOllamaMessage(parsed.Message),
		FinishReason:     finishReason(parsed),
		PromptTokens:     parsed.PromptEvalCount,
		CompletionTokens: parsed.EvalCount,
	}, nil
}

// StreamChat consumes Ollama's NDJSON stream: one JSON object per line with
// a content delta, then a final object with done=true carrying eval counts.
func (p *Ollama) StreamChat(ctx context.Context, req ChatRequest, onDelta func(string) error) (ChatResponse, error) {
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
		if line == "" {
			continue
		}
		var chunk ollamaChunk
		if err := json.Unmarshal([]byte(line), &chunk); err != nil {
			return ChatResponse{}, fmt.Errorf("decode ollama chunk: %w", err)
		}
		if chunk.Model != "" {
			out.Model = chunk.Model
		}
		if chunk.Message.Content != "" {
			content.WriteString(chunk.Message.Content)
			if onDelta != nil {
				if err := onDelta(chunk.Message.Content); err != nil {
					return ChatResponse{}, err
				}
			}
		}
		for _, tc := range chunk.Message.ToolCalls {
			out.Message.ToolCalls = append(out.Message.ToolCalls, ToolCall{
				Name:      tc.Function.Name,
				Arguments: tc.Function.Arguments,
			})
		}
		if chunk.Done {
			out.FinishReason = finishReason(chunk)
			out.PromptTokens = chunk.PromptEvalCount
			out.CompletionTokens = chunk.EvalCount
			break
		}
	}
	if err := scanner.Err(); err != nil {
		return ChatResponse{}, err
	}
	out.Message.Content = content.String()
	return out, nil
}

func finishReason(chunk ollamaChunk) string {
	if chunk.DoneReason != "" {
		return chunk.DoneReason
	}
	if len(chunk.Message.ToolCalls) > 0 {
		return "tool_calls"
	}
	return "stop"
}
