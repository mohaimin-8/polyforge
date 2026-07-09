// Package replay drives PolyForge with normalized workload traces (W25b).
//
// Input is the five-column trace_event table the research/traces ETL
// pipelines emit. The driver is deliberately in-repo Go rather than k6
// (roadmap deviation, same precedent as cmd/loadgen): determinism is a
// graded requirement — same seed, same exact event sequence — and that is
// easiest to prove with a unit-tested scheduler and a stream digest that
// matches the Python ETL's digest byte for byte.
package replay

import (
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Event is one normalized trace row.
type Event struct {
	TimestampMS       int64
	TenantID          string
	RequestKind       string
	PayloadBytes      int64
	ExpectedLatencyMS float64
}

var validKinds = map[string]bool{
	"crud_read": true, "crud_write": true, "chat": true,
	"embed": true, "agent": true, "batch": true,
}

// Open reads a normalized trace from a .csv or .csv.gz file.
func Open(path string) ([]Event, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer func() { _ = file.Close() }()
	var reader io.Reader = file
	if strings.HasSuffix(path, ".gz") {
		zr, err := gzip.NewReader(file)
		if err != nil {
			return nil, fmt.Errorf("replay: open gzip %s: %w", path, err)
		}
		defer func() { _ = zr.Close() }()
		reader = zr
	}
	return LoadTrace(reader)
}

// LoadTrace parses the CSV interchange format (header required, columns in
// canonical order). Rows must already be time-sorted — the ETL guarantees
// it and the loader enforces rather than repairs, so a corrupted trace
// fails loudly instead of replaying in the wrong order.
func LoadTrace(r io.Reader) ([]Event, error) {
	cr := csv.NewReader(r)
	header, err := cr.Read()
	if err != nil {
		return nil, fmt.Errorf("replay: read header: %w", err)
	}
	want := "timestamp_ms,tenant_id,request_kind,payload_bytes,expected_latency_ms"
	if strings.Join(header, ",") != want {
		return nil, fmt.Errorf("replay: unexpected header %q, want %q", strings.Join(header, ","), want)
	}
	var events []Event
	for line := 2; ; line++ {
		record, err := cr.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("replay: line %d: %w", line, err)
		}
		ts, err1 := strconv.ParseInt(record[0], 10, 64)
		payload, err2 := strconv.ParseInt(record[3], 10, 64)
		latency, err3 := strconv.ParseFloat(record[4], 64)
		if err1 != nil || err2 != nil || err3 != nil {
			return nil, fmt.Errorf("replay: line %d: malformed numeric field", line)
		}
		event := Event{
			TimestampMS:       ts,
			TenantID:          record[1],
			RequestKind:       record[2],
			PayloadBytes:      payload,
			ExpectedLatencyMS: latency,
		}
		if !validKinds[event.RequestKind] {
			return nil, fmt.Errorf("replay: line %d: unknown request_kind %q", line, event.RequestKind)
		}
		if len(events) > 0 && event.TimestampMS < events[len(events)-1].TimestampMS {
			return nil, fmt.Errorf("replay: line %d: timestamps not sorted", line)
		}
		events = append(events, event)
	}
	return events, nil
}

// TraceHash is the digest of the exact event sequence, byte-identical to
// research/traces/common.py stream_hash. A trace file and its Go load
// producing the same digest is the cross-language determinism proof.
func TraceHash(events []Event) string {
	digest := sha256.New()
	for _, e := range events {
		_, _ = fmt.Fprintf(digest, "%d,%s,%s,%d,%s\n",
			e.TimestampMS, e.TenantID, e.RequestKind, e.PayloadBytes,
			strconv.FormatFloat(e.ExpectedLatencyMS, 'f', 3, 64))
	}
	return hex.EncodeToString(digest.Sum(nil))
}

// MixTarget maps replayed traffic onto one PolyForge tenant.
type MixTarget struct {
	TenantID string `json:"tenant_id"`
	APIKey   string `json:"api_key"`
	Weight   int    `json:"weight"`
}

// Mix is the tenant-mix configuration: source-trace tenants are spread
// across the targets in weight proportion, deterministically per seed.
type Mix struct {
	Targets []MixTarget `json:"targets"`
}

func LoadMix(path string) (Mix, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Mix{}, err
	}
	var mix Mix
	if err := json.Unmarshal(raw, &mix); err != nil {
		return Mix{}, fmt.Errorf("replay: parse mix %s: %w", path, err)
	}
	return mix, mix.validate()
}

func (m Mix) validate() error {
	if len(m.Targets) == 0 {
		return fmt.Errorf("replay: mix has no targets")
	}
	for _, t := range m.Targets {
		if t.TenantID == "" || t.Weight <= 0 {
			return fmt.Errorf("replay: mix target needs tenant_id and positive weight")
		}
	}
	return nil
}

func (m Mix) totalWeight() int {
	total := 0
	for _, t := range m.Targets {
		total += t.Weight
	}
	return total
}

// Target picks the destination tenant for a source tenant. The choice is a
// pure function of (seed, source tenant): one source tenant never splits
// across targets mid-run, and rerunning with the same seed reproduces the
// exact assignment.
func (m Mix) Target(seed uint64, source string) MixTarget {
	h := fnv.New64a()
	_, _ = fmt.Fprintf(h, "%d|%s", seed, source)
	slot := int(h.Sum64() % uint64(m.totalWeight()))
	for _, t := range m.Targets {
		slot -= t.Weight
		if slot < 0 {
			return t
		}
	}
	return m.Targets[len(m.Targets)-1]
}

// ScheduledEvent is an event bound to a target tenant and a due offset on
// the replay clock.
type ScheduledEvent struct {
	Event
	Target MixTarget
	Due    time.Duration
	// SourceRPS is the trailing 1s event rate of the source tenant at this
	// point in the trace; it feeds the rps_window telemetry field.
	SourceRPS float64
}

// Schedule maps every event onto the replay clock: due = (ts − first) ÷
// speed. Speed 2.0 replays twice as fast; 0.1 stretches tenfold.
func Schedule(events []Event, mix Mix, seed uint64, speed float64) ([]ScheduledEvent, error) {
	if speed <= 0 {
		return nil, fmt.Errorf("replay: speed must be positive, got %v", speed)
	}
	if err := mix.validate(); err != nil {
		return nil, err
	}
	if len(events) == 0 {
		return nil, nil
	}
	first := events[0].TimestampMS
	scheduled := make([]ScheduledEvent, len(events))
	// Trailing-window start index per tenant for the 1s source rate.
	windowStart := map[string]int{}
	tenantEvents := map[string][]int64{}
	for i, event := range events {
		times := append(tenantEvents[event.TenantID], event.TimestampMS)
		tenantEvents[event.TenantID] = times
		start := windowStart[event.TenantID]
		for times[start] < event.TimestampMS-1000 {
			start++
		}
		windowStart[event.TenantID] = start
		scheduled[i] = ScheduledEvent{
			Event:     event,
			Target:    mix.Target(seed, event.TenantID),
			Due:       time.Duration(float64(event.TimestampMS-first) / speed * float64(time.Millisecond)),
			SourceRPS: float64(len(times) - start),
		}
	}
	return scheduled, nil
}

// ScheduleHash digests the post-mapping schedule — target tenants and due
// offsets included — so two runs with the same trace, mix, seed, and speed
// can be proven identical end to end.
func ScheduleHash(schedule []ScheduledEvent) string {
	digest := sha256.New()
	for _, s := range schedule {
		_, _ = fmt.Fprintf(digest, "%d,%s,%s,%d,%s,%s,%d,%s\n",
			s.TimestampMS, s.TenantID, s.RequestKind, s.PayloadBytes,
			strconv.FormatFloat(s.ExpectedLatencyMS, 'f', 3, 64),
			s.Target.TenantID, s.Due.Nanoseconds(),
			strconv.FormatFloat(s.SourceRPS, 'f', 3, 64))
	}
	return hex.EncodeToString(digest.Sum(nil))
}

// Sender delivers one scheduled event to the system under test.
type Sender interface {
	Send(ctx context.Context, event ScheduledEvent) error
}

// Stats summarize a run.
type Stats struct {
	Sent    int
	Errors  int
	Tenants map[string]int
	MaxLag  time.Duration
}

// Runner replays a schedule against a Sender in due-time order. Clock and
// sleep are injectable so tests replay instantly.
type Runner struct {
	Now   func() time.Time
	Sleep func(time.Duration)
}

func (r *Runner) Run(ctx context.Context, schedule []ScheduledEvent, sender Sender) (Stats, error) {
	now := r.Now
	if now == nil {
		now = time.Now
	}
	sleep := r.Sleep
	if sleep == nil {
		sleep = time.Sleep
	}
	stats := Stats{Tenants: map[string]int{}}
	start := now()
	for _, event := range schedule {
		if err := ctx.Err(); err != nil {
			return stats, err
		}
		elapsed := now().Sub(start)
		if wait := event.Due - elapsed; wait > 0 {
			sleep(wait)
		} else if lag := -1 * (event.Due - elapsed); lag > stats.MaxLag {
			// Behind schedule: send immediately and record the worst lag —
			// a replay that cannot keep up must say so, not silently
			// stretch the workload.
			stats.MaxLag = lag
		}
		if err := sender.Send(ctx, event); err != nil {
			stats.Errors++
		} else {
			stats.Sent++
			stats.Tenants[event.Target.TenantID]++
		}
	}
	return stats, nil
}

// SortCheck verifies schedule order is non-decreasing in due time; the
// scheduler guarantees it, callers assert it cheaply before long runs.
func SortCheck(schedule []ScheduledEvent) bool {
	return sort.SliceIsSorted(schedule, func(i, j int) bool {
		return schedule[i].Due < schedule[j].Due
	})
}
