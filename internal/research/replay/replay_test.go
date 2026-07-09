package replay

import (
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"polyforge/internal/platform"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// pythonStreamHash is research/traces/common.py stream_hash over
// testdata/sample.csv; regenerating the fixture reprints it. Matching it
// here proves the two languages digest the identical event sequence.
const pythonStreamHash = "e0c004af6efa282266c8189b01e840defb4b45a12a160b823f63b15db3febfa9"

func loadSample(t *testing.T) []Event {
	t.Helper()
	events, err := Open(filepath.Join("testdata", "sample.csv"))
	if err != nil {
		t.Fatalf("Open sample: %v", err)
	}
	if len(events) != 5 {
		t.Fatalf("want 5 events, got %d", len(events))
	}
	return events
}

func TestTraceHashMatchesPythonETL(t *testing.T) {
	if got := TraceHash(loadSample(t)); got != pythonStreamHash {
		t.Fatalf("cross-language hash mismatch:\n go     %s\n python %s", got, pythonStreamHash)
	}
}

func TestLoadTraceRejectsCorruptInput(t *testing.T) {
	cases := map[string]string{
		"wrong header":    "a,b,c,d,e\n1,x,chat,1,1\n",
		"unknown kind":    "timestamp_ms,tenant_id,request_kind,payload_bytes,expected_latency_ms\n1,x,ddos,1,1\n",
		"unsorted":        "timestamp_ms,tenant_id,request_kind,payload_bytes,expected_latency_ms\n5,x,chat,1,1\n1,x,chat,1,1\n",
		"malformed float": "timestamp_ms,tenant_id,request_kind,payload_bytes,expected_latency_ms\n1,x,chat,1,abc\n",
	}
	for name, input := range cases {
		if _, err := LoadTrace(strings.NewReader(input)); err == nil {
			t.Errorf("%s: want error, got nil", name)
		}
	}
}

func TestOpenReadsGzip(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("testdata", "sample.csv"))
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	gzPath := filepath.Join(dir, "sample.csv.gz")
	file, err := os.Create(gzPath)
	if err != nil {
		t.Fatal(err)
	}
	zw := gzip.NewWriter(file)
	if _, err := zw.Write(raw); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	events, err := Open(gzPath)
	if err != nil {
		t.Fatalf("Open gz: %v", err)
	}
	if TraceHash(events) != pythonStreamHash {
		t.Fatal("gzip round-trip changed the event stream")
	}
}

func testMix() Mix {
	return Mix{Targets: []MixTarget{
		{TenantID: "acme", APIKey: "k1", Weight: 3},
		{TenantID: "globex", APIKey: "k2", Weight: 1},
	}}
}

func TestScheduleIsDeterministicPerSeed(t *testing.T) {
	events := loadSample(t)
	first, err := Schedule(events, testMix(), 42, 2.0)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Schedule(events, testMix(), 42, 2.0)
	if err != nil {
		t.Fatal(err)
	}
	if ScheduleHash(first) != ScheduleHash(second) {
		t.Fatal("same seed produced different schedules")
	}
	other, err := Schedule(events, testMix(), 43, 2.0)
	if err != nil {
		t.Fatal(err)
	}
	if ScheduleHash(first) == ScheduleHash(other) {
		t.Fatal("different seeds should not collide on this fixture")
	}
	if !SortCheck(first) {
		t.Fatal("schedule is not due-time sorted")
	}
}

func TestScheduleSpeedScalesDueTimes(t *testing.T) {
	events := loadSample(t)
	normal, _ := Schedule(events, testMix(), 42, 1.0)
	fast, _ := Schedule(events, testMix(), 42, 4.0)
	// Last event: 5000ms after the first → 4000ms span at 1x, 1000ms at 4x.
	if want := 4 * time.Second; normal[len(normal)-1].Due != want {
		t.Fatalf("1x due = %s, want %s", normal[len(normal)-1].Due, want)
	}
	if want := time.Second; fast[len(fast)-1].Due != want {
		t.Fatalf("4x due = %s, want %s", fast[len(fast)-1].Due, want)
	}
}

func TestScheduleComputesTrailingSourceRPS(t *testing.T) {
	events := loadSample(t)
	schedule, _ := Schedule(events, testMix(), 42, 1.0)
	// alpha events at 1000, 2000, 2100: at 2100 the trailing 1s window
	// [1100, 2100] holds two alpha events.
	if got := schedule[3].SourceRPS; got != 2 {
		t.Fatalf("SourceRPS at t=2100 = %v, want 2", got)
	}
	if got := schedule[0].SourceRPS; got != 1 {
		t.Fatalf("SourceRPS of first event = %v, want 1", got)
	}
}

func TestMixTargetIsStablePerSourceTenant(t *testing.T) {
	mix := testMix()
	for _, source := range []string{"alpha", "beta", "gamma", "delta"} {
		first := mix.Target(7, source)
		for i := 0; i < 10; i++ {
			if mix.Target(7, source) != first {
				t.Fatalf("target for %s not stable", source)
			}
		}
	}
	// Across many source tenants the weights should roughly hold (3:1).
	counts := map[string]int{}
	for i := 0; i < 4000; i++ {
		source := fmt.Sprintf("tenant-%04d", i)
		counts[mix.Target(7, source).TenantID]++
	}
	acmeShare := float64(counts["acme"]) / 4000
	if acmeShare < 0.6 || acmeShare > 0.9 {
		t.Fatalf("weighted assignment off: acme share %.2f, want ~0.75", acmeShare)
	}
}

// instantClock replays without real sleeping and records the virtual time.
type instantClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *instantClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *instantClock) Sleep(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = c.now.Add(d)
}

type captureSender struct {
	events []ScheduledEvent
}

func (c *captureSender) Send(_ context.Context, event ScheduledEvent) error {
	c.events = append(c.events, event)
	return nil
}

func TestRunnerSendsEverythingInOrder(t *testing.T) {
	events := loadSample(t)
	schedule, _ := Schedule(events, testMix(), 42, 10.0)
	clock := &instantClock{now: time.Unix(0, 0)}
	sender := &captureSender{}
	runner := &Runner{Now: clock.Now, Sleep: clock.Sleep}
	stats, err := runner.Run(context.Background(), schedule, sender)
	if err != nil {
		t.Fatal(err)
	}
	if stats.Sent != len(schedule) || stats.Errors != 0 {
		t.Fatalf("stats = %+v", stats)
	}
	if len(sender.events) != len(schedule) {
		t.Fatalf("sent %d of %d", len(sender.events), len(schedule))
	}
	for i := range sender.events {
		if sender.events[i].Due != schedule[i].Due {
			t.Fatalf("event %d out of order", i)
		}
	}
	// Virtual clock advanced exactly to the last due offset: the trace
	// spans 1000ms..5000ms → 4000ms, at 10x = 400ms.
	if got := clock.Now().Sub(time.Unix(0, 0)); got != 400*time.Millisecond {
		t.Fatalf("virtual elapsed = %s, want 400ms", got)
	}
}

func TestReplayEndToEndAgainstControlPlane(t *testing.T) {
	ctx := context.Background()
	tenantStore := tenant.NewStore()
	if _, err := tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"}); err != nil {
		t.Fatal(err)
	}
	if _, err := tenantStore.CreateTenant(ctx, tenant.Tenant{ID: "globex", Name: "Globex"}); err != nil {
		t.Fatal(err)
	}
	acmeKey, _ := tenantStore.CreateAPIKey(ctx, "acme", "replay", tenant.ScopeFull)
	globexKey, _ := tenantStore.CreateAPIKey(ctx, "globex", "replay", tenant.ScopeFull)
	telemetryStore := telemetry.NewStore(1000)
	server := httptest.NewServer(platform.NewServer(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		tenantStore,
		telemetryStore,
		platform.Config{},
	).Handler())
	defer server.Close()

	mix := Mix{Targets: []MixTarget{
		{TenantID: "acme", APIKey: acmeKey.Secret, Weight: 1},
		{TenantID: "globex", APIKey: globexKey.Secret, Weight: 1},
	}}
	schedule, err := Schedule(loadSample(t), mix, 42, 1000.0)
	if err != nil {
		t.Fatal(err)
	}
	runner := &Runner{}
	stats, err := runner.Run(context.Background(), schedule, &HTTPSender{BaseURL: server.URL})
	if err != nil {
		t.Fatal(err)
	}
	if stats.Sent != 5 || stats.Errors != 0 {
		t.Fatalf("replay against control plane: %+v", stats)
	}
}
