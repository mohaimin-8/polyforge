package analytics

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"polyforge/internal/telemetry"
)

func sampleEvent(tenant string) telemetry.Event {
	return telemetry.Event{
		TenantID:         tenant,
		Service:          "ai-gateway",
		Timestamp:        time.Date(2026, 7, 10, 12, 30, 45, 123_000_000, time.UTC),
		RPSWindow:        41.5,
		PayloadBytes:     2048,
		LatencyMS:        87.25,
		CacheHit:         true,
		EmbeddingDensity: 0.82,
		ModelTier:        "local",
		ChildSpans:       3,
	}
}

func TestClickHouseSinkWritesJSONEachRow(t *testing.T) {
	var mu sync.Mutex
	var gotQuery, gotBody, gotUser, gotKey string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		gotQuery = r.URL.Query().Get("query")
		gotBody = string(body)
		gotUser = r.Header.Get("X-ClickHouse-User")
		gotKey = r.Header.Get("X-ClickHouse-Key")
		mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	defer ts.Close()

	url := strings.Replace(ts.URL, "http://", "http://polyforge:secret@", 1)
	sink, err := NewClickHouseSink(ClickHouseConfig{URL: url})
	if err != nil {
		t.Fatalf("NewClickHouseSink: %v", err)
	}
	if err := sink.WriteBatch(context.Background(), []telemetry.Event{sampleEvent("acme"), sampleEvent("globex")}); err != nil {
		t.Fatalf("WriteBatch: %v", err)
	}

	mu.Lock()
	defer mu.Unlock()
	if !strings.HasPrefix(gotQuery, "INSERT INTO polyforge.requests_telemetry (") || !strings.HasSuffix(gotQuery, "FORMAT JSONEachRow") {
		t.Fatalf("unexpected insert statement: %q", gotQuery)
	}
	if gotUser != "polyforge" || gotKey != "secret" {
		t.Fatalf("credentials not forwarded: user=%q key=%q", gotUser, gotKey)
	}
	lines := strings.Split(strings.TrimSpace(gotBody), "\n")
	if len(lines) != 2 {
		t.Fatalf("want 2 JSONEachRow lines, got %d: %q", len(lines), gotBody)
	}
	want := `{"tenant_id":"acme","service":"ai-gateway","timestamp":"2026-07-10 12:30:45.123","rps_window":41.5,"payload_bytes":2048,"latency_ms":87.25,"cache_hit":true,"embedding_density":0.82,"model_tier":"local","child_spans":3}`
	if lines[0] != want {
		t.Fatalf("row mismatch:\n got %s\nwant %s", lines[0], want)
	}
}

func TestClickHouseSinkSurfacesServerErrors(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "Code: 241. DB::Exception: Memory limit exceeded", http.StatusInternalServerError)
	}))
	defer ts.Close()
	sink, err := NewClickHouseSink(ClickHouseConfig{URL: ts.URL})
	if err != nil {
		t.Fatalf("NewClickHouseSink: %v", err)
	}
	err = sink.WriteBatch(context.Background(), []telemetry.Event{sampleEvent("acme")})
	if err == nil || !strings.Contains(err.Error(), "Memory limit") {
		t.Fatalf("want surfaced server error, got %v", err)
	}
}

func TestClickHouseSinkRejectsUnsafeIdentifiers(t *testing.T) {
	if _, err := NewClickHouseSink(ClickHouseConfig{URL: "http://localhost:8123", Table: "t; DROP TABLE x"}); err == nil {
		t.Fatal("want identifier rejection, got nil")
	}
	if _, err := NewClickHouseSink(ClickHouseConfig{URL: "://bad"}); err == nil {
		t.Fatal("want URL rejection, got nil")
	}
}

func TestRowJSONEscapesControlCharacters(t *testing.T) {
	event := sampleEvent("acme")
	event.Service = "svc\"with\\quotes\nand newline"
	row := rowJSON(event)
	if !strings.Contains(row, `"service":"svc\"with\\quotes\nand newline"`) {
		t.Fatalf("escaping broken: %s", row)
	}
}

// recordingSink captures batches and can be told to fail some number of
// attempts first.
type recordingSink struct {
	mu       sync.Mutex
	batches  [][]telemetry.Event
	failures int
}

func (r *recordingSink) WriteBatch(_ context.Context, events []telemetry.Event) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.failures > 0 {
		r.failures--
		return errors.New("transient failure")
	}
	copied := make([]telemetry.Event, len(events))
	copy(copied, events)
	r.batches = append(r.batches, copied)
	return nil
}

func (r *recordingSink) totalEvents() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	n := 0
	for _, batch := range r.batches {
		n += len(batch)
	}
	return n
}

func TestBatcherFlushesBySizeAndDrainsOnClose(t *testing.T) {
	sink := &recordingSink{}
	batcher := NewBatcher(sink, nil, BatcherConfig{MaxBatch: 10, FlushInterval: time.Hour})
	for i := 0; i < 25; i++ {
		if !batcher.Enqueue(sampleEvent("acme")) {
			t.Fatalf("enqueue %d rejected", i)
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := batcher.Close(ctx); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if got := sink.totalEvents(); got != 25 {
		t.Fatalf("want all 25 events delivered, got %d", got)
	}
	if batcher.Written() != 25 || batcher.Dropped() != 0 {
		t.Fatalf("counters: written=%d dropped=%d", batcher.Written(), batcher.Dropped())
	}
}

func TestBatcherRetriesTransientFailures(t *testing.T) {
	sink := &recordingSink{failures: 2}
	batcher := NewBatcher(sink, nil, BatcherConfig{MaxBatch: 5, FlushInterval: time.Hour, RetryBase: time.Millisecond})
	for i := 0; i < 5; i++ {
		batcher.Enqueue(sampleEvent("acme"))
	}
	deadline := time.Now().Add(5 * time.Second)
	for sink.totalEvents() < 5 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if got := sink.totalEvents(); got != 5 {
		t.Fatalf("want 5 events after retries, got %d", got)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_ = batcher.Close(ctx)
}

func TestBatcherCountsDropsWhenQueueIsFull(t *testing.T) {
	block := make(chan struct{})
	sink := &blockingSink{unblock: block}
	batcher := NewBatcher(sink, nil, BatcherConfig{QueueCapacity: 4, MaxBatch: 1, FlushInterval: time.Hour})
	// One event occupies the sink; the queue holds 4; everything after that
	// must be rejected without blocking.
	accepted := 0
	for i := 0; i < 32; i++ {
		if batcher.Enqueue(sampleEvent("acme")) {
			accepted++
		}
	}
	if accepted >= 32 {
		t.Fatal("expected some events to be rejected by the full queue")
	}
	if batcher.Dropped() == 0 {
		t.Fatal("dropped counter must record queue-full rejections")
	}
	close(block)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = batcher.Close(ctx)
}

type blockingSink struct {
	unblock chan struct{}
	once    sync.Once
	blocked bool
	mu      sync.Mutex
}

func (b *blockingSink) WriteBatch(ctx context.Context, events []telemetry.Event) error {
	b.mu.Lock()
	first := !b.blocked
	b.blocked = true
	b.mu.Unlock()
	if first {
		select {
		case <-b.unblock:
		case <-ctx.Done():
		}
	}
	return nil
}

func TestBatcherRejectsAfterClose(t *testing.T) {
	sink := &recordingSink{}
	batcher := NewBatcher(sink, nil, BatcherConfig{})
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_ = batcher.Close(ctx)
	if batcher.Enqueue(sampleEvent("acme")) {
		t.Fatal("Enqueue after Close must report a drop")
	}
	if batcher.Dropped() == 0 {
		t.Fatal("post-close enqueue must count as dropped")
	}
}
