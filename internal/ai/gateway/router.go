package gateway

import (
	"fmt"
	"sync"
	"time"
)

// Backend couples a named Provider with the attributes routing decides on.
// Cost is a blended prompt+completion estimate; exact split pricing adds
// precision the routing decision does not need.
type Backend struct {
	Name            string
	Provider        Provider
	CostPer1MTokens float64
}

// RoutingRule sends matching requests to one backend. Zero values match
// anything, so {Backend: "local"} alone is a catch-all.
type RoutingRule struct {
	// Plan matches the tenant's billing plan exactly; "" matches any plan.
	Plan string `json:"plan,omitempty"`
	// MinPromptChars/MaxPromptChars bound the total prompt size the rule
	// applies to. MaxPromptChars = 0 means unbounded above.
	MinPromptChars int `json:"min_prompt_chars,omitempty"`
	MaxPromptChars int `json:"max_prompt_chars,omitempty"`
	// Backend names the Backend that wins when this rule matches.
	Backend string `json:"backend"`
}

// RouterConfig is the routing policy: ordered rules, a fallback, and an
// optional per-tenant daily budget. This policy is the knob the JCAC
// controller (M8) will turn; keeping it declarative is what makes it
// optimizable.
type RouterConfig struct {
	Rules   []RoutingRule `json:"rules"`
	Default string        `json:"default"`
	// DailyBudgetUSD caps estimated per-tenant spend per UTC day. Once a
	// tenant crosses it, requests route to the cheapest backend regardless
	// of plan. 0 disables budget enforcement.
	DailyBudgetUSD float64 `json:"daily_budget_usd,omitempty"`
}

// Decision reports which backend won and why, so the gateway can expose the
// choice in a response header and the benchmark can attribute cost.
type Decision struct {
	Backend Backend
	Reason  string
}

// Router picks a backend per request from tenant plan, prompt length, and
// remaining budget (roadmap W20). It implements no Provider itself: the
// server asks it for a Decision, then calls the chosen Provider.
type Router struct {
	cfg      RouterConfig
	backends map[string]Backend
	cheapest Backend

	mu    sync.Mutex
	spend map[string]*tenantSpend
}

type tenantSpend struct {
	day time.Time
	usd float64
}

func NewRouter(cfg RouterConfig, backends ...Backend) (*Router, error) {
	if len(backends) == 0 {
		return nil, fmt.Errorf("router needs at least one backend")
	}
	byName := make(map[string]Backend, len(backends))
	cheapest := backends[0]
	for _, b := range backends {
		if b.Name == "" || b.Provider == nil {
			return nil, fmt.Errorf("backend needs a name and a provider")
		}
		if _, dup := byName[b.Name]; dup {
			return nil, fmt.Errorf("duplicate backend %q", b.Name)
		}
		byName[b.Name] = b
		if b.CostPer1MTokens < cheapest.CostPer1MTokens {
			cheapest = b
		}
	}
	if cfg.Default == "" {
		cfg.Default = backends[0].Name
	}
	if _, ok := byName[cfg.Default]; !ok {
		return nil, fmt.Errorf("default backend %q is not registered", cfg.Default)
	}
	for _, rule := range cfg.Rules {
		if _, ok := byName[rule.Backend]; !ok {
			return nil, fmt.Errorf("rule references unknown backend %q", rule.Backend)
		}
	}
	return &Router{
		cfg:      cfg,
		backends: byName,
		cheapest: cheapest,
		spend:    make(map[string]*tenantSpend),
	}, nil
}

// Route picks the backend for one request. Budget exhaustion overrides the
// rule table: a tenant past its daily budget gets the cheapest backend, not
// an error, because degraded quality beats an outage for chat workloads.
func (r *Router) Route(tenantID, plan string, promptChars int) Decision {
	if r.cfg.DailyBudgetUSD > 0 && r.SpentToday(tenantID) >= r.cfg.DailyBudgetUSD {
		return Decision{Backend: r.cheapest, Reason: "budget-exhausted"}
	}
	for i, rule := range r.cfg.Rules {
		if rule.Plan != "" && rule.Plan != plan {
			continue
		}
		if promptChars < rule.MinPromptChars {
			continue
		}
		if rule.MaxPromptChars > 0 && promptChars > rule.MaxPromptChars {
			continue
		}
		return Decision{Backend: r.backends[rule.Backend], Reason: fmt.Sprintf("rule-%d", i)}
	}
	return Decision{Backend: r.backends[r.cfg.Default], Reason: "default"}
}

// RecordUsage charges a completed request against the tenant's daily
// budget using the backend's blended token price.
func (r *Router) RecordUsage(tenantID, backend string, promptTokens, completionTokens int) {
	b, ok := r.backends[backend]
	if !ok || b.CostPer1MTokens == 0 {
		return
	}
	cost := float64(promptTokens+completionTokens) / 1e6 * b.CostPer1MTokens
	r.mu.Lock()
	defer r.mu.Unlock()
	day := time.Now().UTC().Truncate(24 * time.Hour)
	s := r.spend[tenantID]
	if s == nil || !s.day.Equal(day) {
		s = &tenantSpend{day: day}
		r.spend[tenantID] = s
	}
	s.usd += cost
}

// SpentToday reports the tenant's estimated spend for the current UTC day.
func (r *Router) SpentToday(tenantID string) float64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	s := r.spend[tenantID]
	if s == nil || !s.day.Equal(time.Now().UTC().Truncate(24*time.Hour)) {
		return 0
	}
	return s.usd
}

// Backends lists the registered backends; the benchmark harness iterates
// them so the CSV covers exactly what the router can choose between.
func (r *Router) Backends() []Backend {
	out := make([]Backend, 0, len(r.backends))
	for _, b := range r.backends {
		out = append(out, b)
	}
	return out
}
