package events_test

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"testing"
	"time"

	natsserver "github.com/nats-io/nats-server/v2/server"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"

	"polyforge/internal/events"
	"polyforge/internal/tenant"
)

// startBackbone boots a full NATS server with JetStream inside the test
// process (ADR 0006: local verification without Docker).
func startBackbone(t *testing.T) *events.Backbone {
	t.Helper()
	srv, err := natsserver.NewServer(&natsserver.Options{
		Port:      -1,
		JetStream: true,
		StoreDir:  t.TempDir(),
	})
	if err != nil {
		t.Fatalf("create embedded NATS server: %v", err)
	}
	go srv.Start()
	if !srv.ReadyForConnections(10 * time.Second) {
		t.Fatal("embedded NATS server did not become ready")
	}
	t.Cleanup(srv.Shutdown)

	nc, err := nats.Connect(srv.ClientURL())
	if err != nil {
		t.Fatalf("connect to embedded NATS: %v", err)
	}
	t.Cleanup(nc.Close)

	backbone, err := events.NewBackbone(t.Context(), nc, events.BackboneConfig{
		Storage: jetstream.MemoryStorage,
	})
	if err != nil {
		t.Fatalf("create backbone: %v", err)
	}
	return backbone
}

func TestSubjectLayout(t *testing.T) {
	got := events.Subject("acme", events.ActionProjectCreated)
	want := "polyforge.audit.acme.project:created"
	if got != want {
		t.Fatalf("subject = %q, want %q", got, want)
	}
}

func TestRelayDrainsOutboxToJetStream(t *testing.T) {
	backbone := startBackbone(t)
	store := tenant.NewStore()
	ctx := t.Context()

	if _, _, err := store.ProvisionTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"}, "bootstrap"); err != nil {
		t.Fatalf("provision tenant: %v", err)
	}
	if _, err := store.CreateProject(ctx, tenant.Project{ID: "web", TenantID: "acme", Name: "Web"}); err != nil {
		t.Fatalf("create project: %v", err)
	}

	relay := events.NewRelay(store, backbone, slog.Default(), time.Second)
	if err := relay.DrainOnce(ctx); err != nil {
		t.Fatalf("drain outbox: %v", err)
	}

	count, err := backbone.MessageCount(ctx)
	if err != nil {
		t.Fatalf("stream info: %v", err)
	}
	if count != 2 {
		t.Fatalf("stream holds %d messages, want 2 (provisioned + project.created)", count)
	}

	// A second pass must be a no-op: everything is marked published.
	if err := relay.DrainOnce(ctx); err != nil {
		t.Fatalf("second drain: %v", err)
	}
	if count, _ = backbone.MessageCount(ctx); count != 2 {
		t.Fatalf("second drain grew the stream to %d messages", count)
	}
}

// failOnceOutbox simulates the relay crashing between publish and
// MarkPublished: the first MarkPublished is dropped, so the next pass
// republishes the same events.
type failOnceOutbox struct {
	events.Outbox
	failed bool
}

func (f *failOnceOutbox) MarkPublished(ctx context.Context, ids []string, at time.Time) error {
	if !f.failed {
		f.failed = true
		return errors.New("simulated crash before marking published")
	}
	return f.Outbox.MarkPublished(ctx, ids, at)
}

func TestRelayRetryAfterCrashDoesNotDuplicate(t *testing.T) {
	backbone := startBackbone(t)
	store := tenant.NewStore()
	ctx := t.Context()

	if _, err := store.CreateTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"}); err != nil {
		t.Fatalf("create tenant: %v", err)
	}

	outbox := &failOnceOutbox{Outbox: store}
	relay := events.NewRelay(outbox, backbone, slog.Default(), time.Second)

	if err := relay.DrainOnce(ctx); err == nil {
		t.Fatal("first drain should surface the simulated crash")
	}
	if err := relay.DrainOnce(ctx); err != nil {
		t.Fatalf("retry drain: %v", err)
	}

	// The event was published twice with the same Nats-Msg-Id; JetStream's
	// duplicate window must have dropped the second copy.
	count, err := backbone.MessageCount(ctx)
	if err != nil {
		t.Fatalf("stream info: %v", err)
	}
	if count != 1 {
		t.Fatalf("stream holds %d messages, want exactly 1 after crash-retry", count)
	}
}

func TestProjectionRebuildFromReplayIsByteIdentical(t *testing.T) {
	backbone := startBackbone(t)
	store := tenant.NewStore()
	ctx := t.Context()

	if _, _, err := store.ProvisionTenant(ctx, tenant.Tenant{ID: "acme", Name: "Acme"}, "bootstrap"); err != nil {
		t.Fatalf("provision acme: %v", err)
	}
	if _, _, err := store.ProvisionTenant(ctx, tenant.Tenant{ID: "globex", Name: "Globex"}, "bootstrap"); err != nil {
		t.Fatalf("provision globex: %v", err)
	}
	for _, id := range []string{"web", "api", "batch"} {
		if _, err := store.CreateProject(ctx, tenant.Project{ID: id, TenantID: "acme", Name: id}); err != nil {
			t.Fatalf("create project %s: %v", id, err)
		}
	}
	if err := store.DeleteProject(ctx, "acme", "batch"); err != nil {
		t.Fatalf("delete project: %v", err)
	}
	key, err := store.CreateAPIKey(ctx, "globex", "ci", tenant.ScopeRead)
	if err != nil {
		t.Fatalf("create key: %v", err)
	}
	if err := store.RevokeAPIKey(ctx, "globex", key.ID); err != nil {
		t.Fatalf("revoke key: %v", err)
	}

	relay := events.NewRelay(store, backbone, slog.Default(), time.Second)
	if err := relay.DrainOnce(ctx); err != nil {
		t.Fatalf("drain outbox: %v", err)
	}

	first := events.NewProjection()
	if err := backbone.Replay(ctx, func(e events.Event) error { first.Apply(e); return nil }); err != nil {
		t.Fatalf("first replay: %v", err)
	}
	second := events.NewProjection()
	if err := backbone.Replay(ctx, func(e events.Event) error { second.Apply(e); return nil }); err != nil {
		t.Fatalf("second replay: %v", err)
	}

	firstJSON, err := first.JSON()
	if err != nil {
		t.Fatalf("serialize first projection: %v", err)
	}
	secondJSON, err := second.JSON()
	if err != nil {
		t.Fatalf("serialize second projection: %v", err)
	}
	if !bytes.Equal(firstJSON, secondJSON) {
		t.Fatalf("projection rebuild diverged:\nfirst:  %s\nsecond: %s", firstJSON, secondJSON)
	}

	// The read model must agree with the write model it was derived from.
	acme, ok := first.Summary("acme")
	if !ok {
		t.Fatal("projection is missing tenant acme")
	}
	if acme.Projects != 2 {
		t.Fatalf("acme projects = %d, want 2 (three created, one deleted)", acme.Projects)
	}
	if acme.ActiveAPIKeys != 1 {
		t.Fatalf("acme active keys = %d, want 1 (bootstrap)", acme.ActiveAPIKeys)
	}
	globex, _ := first.Summary("globex")
	if globex.ActiveAPIKeys != 1 {
		t.Fatalf("globex active keys = %d, want 1 (bootstrap; ci key revoked)", globex.ActiveAPIKeys)
	}
}
