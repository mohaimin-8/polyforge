package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
	"unicode"

	"polyforge/internal/tenant"
)

// --- polyforge_query -------------------------------------------------------

// QueryTool searches the calling tenant's projects. It goes through the
// same repository as the HTTP API, so tenant scoping is inherited, not
// reimplemented.
type QueryTool struct {
	Repo tenant.Repository
}

func (t *QueryTool) Name() string { return "polyforge_query" }
func (t *QueryTool) Description() string {
	return "Search the tenant's projects by name. Returns matching projects as JSON."
}
func (t *QueryTool) Schema() json.RawMessage {
	return json.RawMessage(`{"type":"object","properties":{"query":{"type":"string","description":"substring to match against project names; empty lists all projects"}},"required":[]}`)
}

func (t *QueryTool) Call(ctx context.Context, tenantID string, args json.RawMessage) (string, error) {
	var input struct {
		Query string `json:"query"`
	}
	if len(args) > 0 {
		if err := json.Unmarshal(args, &input); err != nil {
			return "", fmt.Errorf("invalid arguments: %w", err)
		}
	}
	page, err := t.Repo.ListProjects(ctx, tenantID, tenant.PageRequest{Limit: tenant.MaxPageSize})
	if err != nil {
		return "", err
	}
	needle := strings.ToLower(strings.TrimSpace(input.Query))
	matches := make([]tenant.Project, 0, len(page.Items))
	for _, p := range page.Items {
		if needle == "" || strings.Contains(strings.ToLower(p.Name), needle) || strings.Contains(strings.ToLower(p.ID), needle) {
			matches = append(matches, p)
		}
	}
	out, err := json.Marshal(matches)
	return string(out), err
}

// --- polyforge_calc --------------------------------------------------------

// CalcTool evaluates arithmetic expressions with a small recursive-descent
// parser — no eval, no external process.
type CalcTool struct{}

func (CalcTool) Name() string { return "polyforge_calc" }
func (CalcTool) Description() string {
	return "Evaluate an arithmetic expression supporting + - * / ^ and parentheses."
}
func (CalcTool) Schema() json.RawMessage {
	return json.RawMessage(`{"type":"object","properties":{"expression":{"type":"string","description":"arithmetic expression, e.g. (2+3)*4^2"}},"required":["expression"]}`)
}

func (CalcTool) Call(_ context.Context, _ string, args json.RawMessage) (string, error) {
	var input struct {
		Expression string `json:"expression"`
	}
	if err := json.Unmarshal(args, &input); err != nil {
		return "", fmt.Errorf("invalid arguments: %w", err)
	}
	value, err := evalExpression(input.Expression)
	if err != nil {
		return "", err
	}
	return strconv.FormatFloat(value, 'g', -1, 64), nil
}

type exprParser struct {
	input []rune
	pos   int
}

func evalExpression(expr string) (float64, error) {
	p := &exprParser{input: []rune(expr)}
	value, err := p.parseSum()
	if err != nil {
		return 0, err
	}
	p.skipSpace()
	if p.pos != len(p.input) {
		return 0, fmt.Errorf("unexpected character %q at position %d", p.input[p.pos], p.pos)
	}
	return value, nil
}

func (p *exprParser) skipSpace() {
	for p.pos < len(p.input) && unicode.IsSpace(p.input[p.pos]) {
		p.pos++
	}
}

func (p *exprParser) peek() rune {
	p.skipSpace()
	if p.pos >= len(p.input) {
		return 0
	}
	return p.input[p.pos]
}

func (p *exprParser) parseSum() (float64, error) {
	left, err := p.parseProduct()
	if err != nil {
		return 0, err
	}
	for {
		switch p.peek() {
		case '+':
			p.pos++
			right, err := p.parseProduct()
			if err != nil {
				return 0, err
			}
			left += right
		case '-':
			p.pos++
			right, err := p.parseProduct()
			if err != nil {
				return 0, err
			}
			left -= right
		default:
			return left, nil
		}
	}
}

func (p *exprParser) parseProduct() (float64, error) {
	left, err := p.parsePower()
	if err != nil {
		return 0, err
	}
	for {
		switch p.peek() {
		case '*':
			p.pos++
			right, err := p.parsePower()
			if err != nil {
				return 0, err
			}
			left *= right
		case '/':
			p.pos++
			right, err := p.parsePower()
			if err != nil {
				return 0, err
			}
			if right == 0 {
				return 0, errors.New("division by zero")
			}
			left /= right
		default:
			return left, nil
		}
	}
}

func (p *exprParser) parsePower() (float64, error) {
	base, err := p.parseUnary()
	if err != nil {
		return 0, err
	}
	if p.peek() == '^' {
		p.pos++
		// Right-associative: 2^3^2 is 2^(3^2).
		exponent, err := p.parsePower()
		if err != nil {
			return 0, err
		}
		return math.Pow(base, exponent), nil
	}
	return base, nil
}

func (p *exprParser) parseUnary() (float64, error) {
	if p.peek() == '-' {
		p.pos++
		value, err := p.parseUnary()
		return -value, err
	}
	return p.parseAtom()
}

func (p *exprParser) parseAtom() (float64, error) {
	if p.peek() == '(' {
		p.pos++
		value, err := p.parseSum()
		if err != nil {
			return 0, err
		}
		if p.peek() != ')' {
			return 0, errors.New("missing closing parenthesis")
		}
		p.pos++
		return value, nil
	}
	p.skipSpace()
	start := p.pos
	for p.pos < len(p.input) && (unicode.IsDigit(p.input[p.pos]) || p.input[p.pos] == '.') {
		p.pos++
	}
	if start == p.pos {
		return 0, fmt.Errorf("expected a number at position %d", start)
	}
	return strconv.ParseFloat(string(p.input[start:p.pos]), 64)
}

// --- polyforge_search ------------------------------------------------------

// SearchResult is one web search hit.
type SearchResult struct {
	Title string `json:"title"`
	URL   string `json:"url"`
}

// SearchFunc performs a web search. Injected so tests never touch the
// network and the engine (DuckDuckGo by default in cmd/ai-gateway) is
// swappable configuration.
type SearchFunc func(ctx context.Context, query string) ([]SearchResult, error)

type SearchTool struct {
	Search SearchFunc
}

func (t *SearchTool) Name() string { return "polyforge_search" }
func (t *SearchTool) Description() string {
	return "Search the web and return result titles and URLs as JSON."
}
func (t *SearchTool) Schema() json.RawMessage {
	return json.RawMessage(`{"type":"object","properties":{"query":{"type":"string","description":"web search query"}},"required":["query"]}`)
}

func (t *SearchTool) Call(ctx context.Context, _ string, args json.RawMessage) (string, error) {
	var input struct {
		Query string `json:"query"`
	}
	if err := json.Unmarshal(args, &input); err != nil {
		return "", fmt.Errorf("invalid arguments: %w", err)
	}
	if strings.TrimSpace(input.Query) == "" {
		return "", errors.New("query is required")
	}
	if t.Search == nil {
		return "", errors.New("web search is not configured")
	}
	results, err := t.Search(ctx, input.Query)
	if err != nil {
		return "", err
	}
	out, err := json.Marshal(results)
	return string(out), err
}
