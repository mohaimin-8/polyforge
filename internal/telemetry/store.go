package telemetry

import (
	"context"
	"math"
	"sort"
	"strings"
	"sync"
	"time"
)

type Event struct {
	TenantID         string    `json:"tenant_id"`
	Service          string    `json:"service"`
	Timestamp        time.Time `json:"timestamp"`
	RPSWindow        float64   `json:"rps_window"`
	PayloadBytes     int       `json:"payload_bytes"`
	LatencyMS        float64   `json:"latency_ms"`
	CacheHit         bool      `json:"cache_hit"`
	EmbeddingDensity float64   `json:"embedding_density"`
	ModelTier        string    `json:"model_tier"`
	ChildSpans       int       `json:"child_spans"`
}

const (
	DefaultFeatureLookback = 15 * time.Minute
	MaxFeatureEvents       = 10000
)

type FeatureQuery struct {
	Service string
	Since   time.Time
	Until   time.Time
}

type FeatureSet struct {
	TenantID string       `json:"tenant_id"`
	Since    time.Time    `json:"since"`
	Until    time.Time    `json:"until"`
	Items    []FeatureRow `json:"items"`
}

type FeatureRow struct {
	TenantID            string         `json:"tenant_id"`
	Service             string         `json:"service"`
	EventCount          int            `json:"event_count"`
	AvgRPSWindow        float64        `json:"avg_rps_window"`
	AvgPayloadBytes     float64        `json:"avg_payload_bytes"`
	AvgLatencyMS        float64        `json:"avg_latency_ms"`
	P95LatencyMS        float64        `json:"p95_latency_ms"`
	CacheHitRate        float64        `json:"cache_hit_rate"`
	AvgEmbeddingDensity float64        `json:"avg_embedding_density"`
	AvgChildSpans       float64        `json:"avg_child_spans"`
	ModelTierCounts     map[string]int `json:"model_tier_counts"`
	FirstEventTimestamp time.Time      `json:"first_event_timestamp"`
	LastEventTimestamp  time.Time      `json:"last_event_timestamp"`
}

type Store struct {
	mu     sync.RWMutex
	events []Event
	limit  int
}

type Repository interface {
	Add(ctx context.Context, e Event) (Event, error)
	RecentByTenant(ctx context.Context, tenantID string, n int) ([]Event, error)
	Features(ctx context.Context, tenantID string, query FeatureQuery) (FeatureSet, error)
}

func NewStore(limit int) *Store {
	if limit <= 0 {
		limit = 10000
	}
	return &Store{limit: limit}
}

func (s *Store) Add(_ context.Context, e Event) (Event, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if e.Timestamp.IsZero() {
		e.Timestamp = time.Now().UTC()
	}
	s.events = append(s.events, e)
	if len(s.events) > s.limit {
		s.events = s.events[len(s.events)-s.limit:]
	}
	return e, nil
}

func (s *Store) RecentByTenant(_ context.Context, tenantID string, n int) ([]Event, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if n <= 0 {
		n = 100
	}
	out := make([]Event, 0, n)
	for i := len(s.events) - 1; i >= 0 && len(out) < n; i-- {
		if s.events[i].TenantID == tenantID {
			out = append(out, s.events[i])
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Timestamp.Before(out[j].Timestamp) })
	return out, nil
}

func (s *Store) Features(_ context.Context, tenantID string, query FeatureQuery) (FeatureSet, error) {
	query = NormalizeFeatureQuery(query)
	s.mu.RLock()
	defer s.mu.RUnlock()

	events := make([]Event, 0)
	for _, event := range s.events {
		if featureEventMatches(event, tenantID, query) {
			events = append(events, event)
			if len(events) >= MaxFeatureEvents {
				break
			}
		}
	}
	return BuildFeatureSet(tenantID, query, events), nil
}

func NormalizeFeatureQuery(query FeatureQuery) FeatureQuery {
	query.Service = strings.TrimSpace(query.Service)
	query.Since = query.Since.UTC()
	query.Until = query.Until.UTC()
	if query.Until.IsZero() {
		query.Until = time.Now().UTC()
	}
	if query.Since.IsZero() {
		query.Since = query.Until.Add(-DefaultFeatureLookback)
	}
	if !query.Since.Before(query.Until) {
		query.Since = query.Until.Add(-DefaultFeatureLookback)
	}
	return query
}

func BuildFeatureSet(tenantID string, query FeatureQuery, events []Event) FeatureSet {
	query = NormalizeFeatureQuery(query)
	groups := make(map[string][]Event)
	for _, event := range events {
		if featureEventMatches(event, tenantID, query) {
			service := strings.TrimSpace(event.Service)
			if service == "" {
				service = "unknown"
			}
			groups[service] = append(groups[service], event)
		}
	}

	services := make([]string, 0, len(groups))
	for service := range groups {
		services = append(services, service)
	}
	sort.Strings(services)

	items := make([]FeatureRow, 0, len(services))
	for _, service := range services {
		items = append(items, buildFeatureRow(tenantID, service, groups[service]))
	}
	return FeatureSet{TenantID: tenantID, Since: query.Since, Until: query.Until, Items: items}
}

func featureEventMatches(event Event, tenantID string, query FeatureQuery) bool {
	if event.TenantID != tenantID {
		return false
	}
	if query.Service != "" && event.Service != query.Service {
		return false
	}
	if event.Timestamp.Before(query.Since) || !event.Timestamp.Before(query.Until) {
		return false
	}
	return true
}

func buildFeatureRow(tenantID, service string, events []Event) FeatureRow {
	row := FeatureRow{
		TenantID:        tenantID,
		Service:         service,
		EventCount:      len(events),
		ModelTierCounts: make(map[string]int),
	}
	if len(events) == 0 {
		return row
	}

	latencies := make([]float64, 0, len(events))
	var rps, payload, latency, cacheHits, density, childSpans float64
	for i, event := range events {
		if i == 0 || event.Timestamp.Before(row.FirstEventTimestamp) {
			row.FirstEventTimestamp = event.Timestamp
		}
		if i == 0 || event.Timestamp.After(row.LastEventTimestamp) {
			row.LastEventTimestamp = event.Timestamp
		}
		rps += event.RPSWindow
		payload += float64(event.PayloadBytes)
		latency += event.LatencyMS
		latencies = append(latencies, event.LatencyMS)
		if event.CacheHit {
			cacheHits++
		}
		density += event.EmbeddingDensity
		childSpans += float64(event.ChildSpans)
		modelTier := strings.TrimSpace(event.ModelTier)
		if modelTier == "" {
			modelTier = "unknown"
		}
		row.ModelTierCounts[modelTier]++
	}
	n := float64(len(events))
	row.AvgRPSWindow = round4(rps / n)
	row.AvgPayloadBytes = round4(payload / n)
	row.AvgLatencyMS = round4(latency / n)
	row.P95LatencyMS = round4(percentile(latencies, 95))
	row.CacheHitRate = round4(cacheHits / n)
	row.AvgEmbeddingDensity = round4(density / n)
	row.AvgChildSpans = round4(childSpans / n)
	return row
}

func percentile(values []float64, p float64) float64 {
	if len(values) == 0 {
		return 0
	}
	sort.Float64s(values)
	index := int(math.Ceil((p/100)*float64(len(values)))) - 1
	if index < 0 {
		index = 0
	}
	if index >= len(values) {
		index = len(values) - 1
	}
	return values[index]
}

func round4(value float64) float64 {
	return math.Round(value*10000) / 10000
}
