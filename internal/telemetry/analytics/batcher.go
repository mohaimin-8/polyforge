package analytics

import (
	"context"
	"log/slog"
	"math"
	"sync"
	"sync/atomic"
	"time"

	"polyforge/internal/telemetry"
)

// BatcherConfig tunes the in-process buffer between the request path and
// the analytical sink.
type BatcherConfig struct {
	// QueueCapacity bounds the buffer; a full queue drops the newest event
	// rather than blocking a request. Default 65536.
	QueueCapacity int
	// MaxBatch is the largest batch handed to the sink. Default 2048.
	MaxBatch int
	// FlushInterval flushes a partial batch that has waited this long.
	// Default 2s.
	FlushInterval time.Duration
	// MaxAttempts bounds delivery attempts per batch (exponential backoff
	// between attempts). Default 5.
	MaxAttempts int
	// RetryBase is the first backoff delay. Default 250ms.
	RetryBase time.Duration
}

func (cfg BatcherConfig) withDefaults() BatcherConfig {
	if cfg.QueueCapacity <= 0 {
		cfg.QueueCapacity = 65536
	}
	if cfg.MaxBatch <= 0 {
		cfg.MaxBatch = 2048
	}
	if cfg.FlushInterval <= 0 {
		cfg.FlushInterval = 2 * time.Second
	}
	if cfg.MaxAttempts <= 0 {
		cfg.MaxAttempts = 5
	}
	if cfg.RetryBase <= 0 {
		cfg.RetryBase = 250 * time.Millisecond
	}
	return cfg
}

// Batcher accumulates events and delivers them to the sink in batches.
//
// Loss semantics are deliberate and documented (ADR 0011): the request path
// must never block on analytics, so Enqueue is non-blocking and a full
// queue or an exhausted retry budget drops events and counts them. The
// no-loss guarantee belongs to the OTel Collector's persistent queue in the
// deployed pipeline; this mirror is the zero-infrastructure path and an
// explicitly best-effort one.
type Batcher struct {
	sink Sink
	log  *slog.Logger
	cfg  BatcherConfig

	queue    chan telemetry.Event
	dropped  atomic.Uint64
	written  atomic.Uint64
	closed   atomic.Bool
	stopOnce sync.Once
	stop     chan struct{}
	done     chan struct{}
}

func NewBatcher(sink Sink, log *slog.Logger, cfg BatcherConfig) *Batcher {
	cfg = cfg.withDefaults()
	b := &Batcher{
		sink:  sink,
		log:   log,
		cfg:   cfg,
		queue: make(chan telemetry.Event, cfg.QueueCapacity),
		stop:  make(chan struct{}),
		done:  make(chan struct{}),
	}
	go b.run()
	return b
}

// Enqueue offers an event to the buffer without blocking. It reports false
// when the event was dropped (queue full or batcher closed).
func (b *Batcher) Enqueue(event telemetry.Event) bool {
	if b.closed.Load() {
		b.dropped.Add(1)
		return false
	}
	select {
	case b.queue <- event:
		return true
	default:
		b.dropped.Add(1)
		return false
	}
}

// Dropped reports events lost to a full queue or exhausted retries.
func (b *Batcher) Dropped() uint64 { return b.dropped.Load() }

// Written reports events successfully delivered to the sink.
func (b *Batcher) Written() uint64 { return b.written.Load() }

// Close stops intake, drains what the context allows, and returns. Events
// still queued when the context expires are counted as dropped.
func (b *Batcher) Close(ctx context.Context) error {
	b.closed.Store(true)
	b.stopOnce.Do(func() { close(b.stop) })
	select {
	case <-b.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (b *Batcher) run() {
	defer close(b.done)
	ticker := time.NewTicker(b.cfg.FlushInterval)
	defer ticker.Stop()
	batch := make([]telemetry.Event, 0, b.cfg.MaxBatch)
	for {
		select {
		case event := <-b.queue:
			batch = append(batch, event)
			if len(batch) >= b.cfg.MaxBatch {
				batch = b.deliver(batch)
			}
		case <-ticker.C:
			if len(batch) > 0 {
				batch = b.deliver(batch)
			}
		case <-b.stop:
			// Drain whatever is already buffered, then flush once.
			for {
				select {
				case event := <-b.queue:
					batch = append(batch, event)
					if len(batch) >= b.cfg.MaxBatch {
						batch = b.deliver(batch)
					}
					continue
				default:
				}
				break
			}
			if len(batch) > 0 {
				b.deliver(batch)
			}
			return
		}
	}
}

// deliver writes the batch with bounded exponential backoff and returns a
// reset slice for reuse.
func (b *Batcher) deliver(batch []telemetry.Event) []telemetry.Event {
	var err error
	for attempt := 0; attempt < b.cfg.MaxAttempts; attempt++ {
		if attempt > 0 {
			backoff := time.Duration(float64(b.cfg.RetryBase) * math.Pow(2, float64(attempt-1)))
			select {
			case <-time.After(backoff):
			case <-b.stop:
				// Shutting down: one final immediate attempt, no more waiting.
			}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		err = b.sink.WriteBatch(ctx, batch)
		cancel()
		if err == nil {
			b.written.Add(uint64(len(batch)))
			return batch[:0]
		}
	}
	b.dropped.Add(uint64(len(batch)))
	if b.log != nil {
		b.log.Warn("analytics batch dropped after retries",
			"events", len(batch), "attempts", b.cfg.MaxAttempts, "error", err)
	}
	return batch[:0]
}
