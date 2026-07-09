package events

import (
	"context"
	"log/slog"
	"time"
)

// Outbox is the store-side half of the transactional outbox. Each store
// (memory, SQLite, PostgreSQL) appends rows inside the same transaction as
// the domain write; only the relay reads them back out.
type Outbox interface {
	// Unpublished returns up to limit events that have not been published,
	// in insertion order.
	Unpublished(ctx context.Context, limit int) ([]Event, error)
	// MarkPublished stamps published_at for the given event IDs.
	MarkPublished(ctx context.Context, ids []string, at time.Time) error
}

// Publisher publishes one event to the backbone. msgID must be set as the
// Nats-Msg-Id header so JetStream deduplicates relay retries.
type Publisher interface {
	Publish(ctx context.Context, subject, msgID string, data []byte) error
}

// Relay drains the outbox to the publisher. It is crash-safe by
// construction: a crash after publish but before MarkPublished republishes
// on the next pass, and JetStream drops the duplicate by message ID.
type Relay struct {
	outbox    Outbox
	publisher Publisher
	log       *slog.Logger
	interval  time.Duration
	batch     int
}

func NewRelay(outbox Outbox, publisher Publisher, log *slog.Logger, interval time.Duration) *Relay {
	if interval <= 0 {
		interval = 250 * time.Millisecond
	}
	if log == nil {
		log = slog.Default()
	}
	return &Relay{outbox: outbox, publisher: publisher, log: log, interval: interval, batch: 256}
}

// Run polls until ctx is cancelled.
func (r *Relay) Run(ctx context.Context) {
	ticker := time.NewTicker(r.interval)
	defer ticker.Stop()
	for {
		if err := r.DrainOnce(ctx); err != nil && ctx.Err() == nil {
			r.log.Warn("outbox relay pass failed; will retry", "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

// DrainOnce publishes every currently-unpublished event. Events are
// published strictly in outbox order; the pass stops at the first publish
// failure so ordering is preserved across retries.
func (r *Relay) DrainOnce(ctx context.Context) error {
	for {
		pending, err := r.outbox.Unpublished(ctx, r.batch)
		if err != nil {
			return err
		}
		if len(pending) == 0 {
			return nil
		}
		published := make([]string, 0, len(pending))
		var publishErr error
		for _, event := range pending {
			data, err := marshalEvent(event)
			if err != nil {
				publishErr = err
				break
			}
			if err := r.publisher.Publish(ctx, event.Subject, event.ID, data); err != nil {
				publishErr = err
				break
			}
			published = append(published, event.ID)
		}
		if len(published) > 0 {
			if err := r.outbox.MarkPublished(ctx, published, time.Now().UTC()); err != nil {
				return err
			}
		}
		if publishErr != nil {
			return publishErr
		}
		if len(pending) < r.batch {
			return nil
		}
	}
}
