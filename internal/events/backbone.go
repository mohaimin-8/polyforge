package events

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// Backbone wraps one JetStream connection plus the POLYFORGE_AUDIT stream.
// It satisfies Publisher for the relay and provides replay for projections.
type Backbone struct {
	nc      *nats.Conn
	ownConn bool
	js      jetstream.JetStream
	stream  jetstream.Stream
}

type BackboneConfig struct {
	// Storage defaults to file storage (ADR 0006); tests use memory.
	Storage jetstream.StorageType
	// MaxAge defaults to the ADR's 30-day retention.
	MaxAge time.Duration
	// Duplicates is the JetStream dedupe window that makes relay retries
	// safe; defaults to 2 minutes.
	Duplicates time.Duration
}

// Connect dials url and ensures the audit stream exists.
func Connect(ctx context.Context, url string, cfg BackboneConfig) (*Backbone, error) {
	nc, err := nats.Connect(url, nats.Name("polyforge-control-plane"))
	if err != nil {
		return nil, fmt.Errorf("connect NATS: %w", err)
	}
	b, err := NewBackbone(ctx, nc, cfg)
	if err != nil {
		nc.Close()
		return nil, err
	}
	b.ownConn = true
	return b, nil
}

// NewBackbone builds on an existing connection (the embedded-server tests
// own their connection) and ensures the audit stream exists.
func NewBackbone(ctx context.Context, nc *nats.Conn, cfg BackboneConfig) (*Backbone, error) {
	if cfg.MaxAge <= 0 {
		cfg.MaxAge = 30 * 24 * time.Hour
	}
	if cfg.Duplicates <= 0 {
		cfg.Duplicates = 2 * time.Minute
	}
	js, err := jetstream.New(nc)
	if err != nil {
		return nil, fmt.Errorf("open JetStream context: %w", err)
	}
	stream, err := js.CreateOrUpdateStream(ctx, jetstream.StreamConfig{
		Name:       StreamName,
		Subjects:   []string{SubjectFilter},
		Storage:    cfg.Storage,
		MaxAge:     cfg.MaxAge,
		Duplicates: cfg.Duplicates,
	})
	if err != nil {
		return nil, fmt.Errorf("ensure stream %s: %w", StreamName, err)
	}
	return &Backbone{nc: nc, js: js, stream: stream}, nil
}

func (b *Backbone) Close() {
	if b.ownConn {
		b.nc.Close()
	}
}

// Publish satisfies Publisher. The message ID is what deduplicates relay
// retries server-side.
func (b *Backbone) Publish(ctx context.Context, subject, msgID string, data []byte) error {
	_, err := b.js.Publish(ctx, subject, data, jetstream.WithMsgID(msgID))
	return err
}

// MessageCount reports how many messages the audit stream currently holds.
func (b *Backbone) MessageCount(ctx context.Context) (uint64, error) {
	info, err := b.stream.Info(ctx)
	if err != nil {
		return 0, err
	}
	return info.State.Msgs, nil
}

// Replay feeds every message currently in the stream, oldest first, through
// fn using a pull consumer with explicit acks (ADR 0006 consumer side).
// It returns once it has processed everything that existed when it started,
// which makes it the rebuild primitive for CQRS projections.
func (b *Backbone) Replay(ctx context.Context, fn func(Event) error) error {
	info, err := b.stream.Info(ctx)
	if err != nil {
		return err
	}
	remaining := info.State.Msgs
	if remaining == 0 {
		return nil
	}
	consumer, err := b.stream.CreateConsumer(ctx, jetstream.ConsumerConfig{
		AckPolicy:     jetstream.AckExplicitPolicy,
		DeliverPolicy: jetstream.DeliverAllPolicy,
		FilterSubject: SubjectFilter,
	})
	if err != nil {
		return fmt.Errorf("create replay consumer: %w", err)
	}
	defer func() { _ = b.stream.DeleteConsumer(context.WithoutCancel(ctx), consumer.CachedInfo().Name) }()

	for remaining > 0 {
		batch := min(remaining, 128)
		msgs, err := consumer.Fetch(int(batch), jetstream.FetchMaxWait(5*time.Second))
		if err != nil {
			return fmt.Errorf("fetch replay batch: %w", err)
		}
		received := uint64(0)
		for msg := range msgs.Messages() {
			event, err := unmarshalEvent(msg.Data())
			if err != nil {
				return fmt.Errorf("decode replayed event: %w", err)
			}
			if err := fn(event); err != nil {
				return err
			}
			if err := msg.Ack(); err != nil {
				return err
			}
			received++
		}
		if err := msgs.Error(); err != nil {
			return err
		}
		if received == 0 {
			return errors.New("replay stalled before reaching stream end")
		}
		remaining -= received
	}
	return nil
}
