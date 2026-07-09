// Package analytics mirrors accepted telemetry events into the analytical
// store (ClickHouse, ADR 0011). The transactional repository remains the
// source of truth for the feature API; this pipeline exists for the
// research-scale queries — 100M-row scans, trace replays, evaluation
// matrices — that a row store cannot serve interactively.
package analytics

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"polyforge/internal/telemetry"
)

// Sink receives batches of telemetry events for analytical storage.
type Sink interface {
	WriteBatch(ctx context.Context, events []telemetry.Event) error
}

// ClickHouseConfig configures the HTTP-interface sink. The HTTP interface is
// chosen over the native protocol deliberately: it needs no driver
// dependency, works through the mesh like every other HTTP call, and
// JSONEachRow keeps the wire format inspectable in a tap.
type ClickHouseConfig struct {
	// URL is the ClickHouse HTTP endpoint, e.g. http://localhost:8123.
	// Credentials may be carried as URL userinfo.
	URL string
	// Database defaults to "polyforge".
	Database string
	// Table defaults to "requests_telemetry".
	Table string
	// Client defaults to an http.Client with a 10s timeout.
	Client *http.Client
}

type ClickHouseSink struct {
	endpoint string
	user     string
	password string
	insert   string
	client   *http.Client
}

func NewClickHouseSink(cfg ClickHouseConfig) (*ClickHouseSink, error) {
	parsed, err := url.Parse(cfg.URL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return nil, fmt.Errorf("analytics: invalid ClickHouse URL %q", cfg.URL)
	}
	sink := &ClickHouseSink{client: cfg.Client}
	if parsed.User != nil {
		sink.user = parsed.User.Username()
		sink.password, _ = parsed.User.Password()
		parsed.User = nil
	}
	if sink.client == nil {
		sink.client = &http.Client{Timeout: 10 * time.Second}
	}
	database := cfg.Database
	if database == "" {
		database = "polyforge"
	}
	table := cfg.Table
	if table == "" {
		table = "requests_telemetry"
	}
	if !validIdentifier(database) || !validIdentifier(table) {
		return nil, fmt.Errorf("analytics: invalid database %q or table %q", database, table)
	}
	sink.insert = fmt.Sprintf(
		"INSERT INTO %s.%s (tenant_id, service, timestamp, rps_window, payload_bytes, latency_ms, cache_hit, embedding_density, model_tier, child_spans) FORMAT JSONEachRow",
		database, table,
	)
	query := url.Values{"query": {sink.insert}}
	parsed.RawQuery = query.Encode()
	parsed.Path = "/"
	sink.endpoint = parsed.String()
	return sink, nil
}

// validIdentifier permits exactly the safe subset used in our DDL; the
// identifiers are interpolated into the INSERT statement, so anything
// broader would be an injection surface.
func validIdentifier(s string) bool {
	if s == "" || len(s) > 64 {
		return false
	}
	for _, r := range s {
		if (r < 'a' || r > 'z') && (r < '0' || r > '9') && r != '_' {
			return false
		}
	}
	return true
}

// WriteBatch inserts one JSONEachRow line per event. ClickHouse treats the
// whole INSERT as atomic for a single block, so a retried batch after a
// transport error may duplicate rows; the analytical queries are
// aggregations over large windows where duplicate-tolerance is acceptable
// and documented (docs/CLICKHOUSE_DESIGN.md).
func (s *ClickHouseSink) WriteBatch(ctx context.Context, events []telemetry.Event) error {
	if len(events) == 0 {
		return nil
	}
	var body strings.Builder
	for _, event := range events {
		body.WriteString(rowJSON(event))
		body.WriteByte('\n')
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.endpoint, strings.NewReader(body.String()))
	if err != nil {
		return fmt.Errorf("analytics: build insert request: %w", err)
	}
	req.Header.Set("Content-Type", "application/x-ndjson")
	if s.user != "" {
		req.Header.Set("X-ClickHouse-User", s.user)
		req.Header.Set("X-ClickHouse-Key", s.password)
	}
	resp, err := s.client.Do(req)
	if err != nil {
		return fmt.Errorf("analytics: insert batch: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return fmt.Errorf("analytics: clickhouse returned %d: %s", resp.StatusCode, strings.TrimSpace(string(detail)))
	}
	return nil
}

// rowJSON renders one event as a JSONEachRow line. Fields are emitted in
// column order with an explicit timestamp format ClickHouse parses into
// DateTime64(3) without best_effort settings.
func rowJSON(event telemetry.Event) string {
	var b strings.Builder
	b.WriteString(`{"tenant_id":`)
	b.WriteString(quoteJSON(event.TenantID))
	b.WriteString(`,"service":`)
	b.WriteString(quoteJSON(event.Service))
	b.WriteString(`,"timestamp":"`)
	b.WriteString(event.Timestamp.UTC().Format("2006-01-02 15:04:05.000"))
	b.WriteString(`","rps_window":`)
	b.WriteString(formatFloat(event.RPSWindow))
	b.WriteString(`,"payload_bytes":`)
	b.WriteString(fmt.Sprintf("%d", event.PayloadBytes))
	b.WriteString(`,"latency_ms":`)
	b.WriteString(formatFloat(event.LatencyMS))
	b.WriteString(`,"cache_hit":`)
	if event.CacheHit {
		b.WriteString("true")
	} else {
		b.WriteString("false")
	}
	b.WriteString(`,"embedding_density":`)
	b.WriteString(formatFloat(event.EmbeddingDensity))
	b.WriteString(`,"model_tier":`)
	b.WriteString(quoteJSON(event.ModelTier))
	b.WriteString(`,"child_spans":`)
	b.WriteString(fmt.Sprintf("%d", event.ChildSpans))
	b.WriteString("}")
	return b.String()
}

func formatFloat(v float64) string {
	return strings.TrimSuffix(strings.TrimRight(fmt.Sprintf("%.6f", v), "0"), ".")
}

func quoteJSON(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			if r < 0x20 {
				fmt.Fprintf(&b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}

// ErrClosed is returned by Enqueue after Close has begun.
var ErrClosed = errors.New("analytics: batcher closed")
