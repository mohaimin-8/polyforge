//go:build ignore

// wireseed provisions the two tenants (victim, attacker) and one API key each
// directly in the ai-gateway's SQLite store, printing the plaintext secrets as
// `victim <secret>` / `attacker <secret>`. It bypasses the control-plane's
// admin/classifier requirements — this is a local wire-attack harness helper
// (PREREG_WIRE_ATTACK.md), not a production path.
//
//	go run research/security/wireseed.go /tmp/wire.db
package main

import (
	"context"
	"fmt"
	"os"

	sqlitestore "polyforge/internal/storage/sqlite"
	"polyforge/internal/tenant"
)

func main() {
	dbPath := "/tmp/wire.db"
	if len(os.Args) > 1 {
		dbPath = os.Args[1]
	}
	ctx := context.Background()
	store, err := sqlitestore.Open(ctx, dbPath)
	if err != nil {
		panic(err)
	}
	defer func() { _ = store.Close() }()

	for _, id := range []string{"victim", "attacker"} {
		if _, err := store.CreateTenant(ctx, tenant.Tenant{
			ID: id, Name: id, Plan: "standard", IsolationMode: "shared",
		}); err != nil {
			panic(fmt.Errorf("create tenant %s: %w", id, err))
		}
		key, err := store.CreateAPIKey(ctx, id, "wire", "full")
		if err != nil {
			panic(fmt.Errorf("create key %s: %w", id, err))
		}
		fmt.Printf("%s %s\n", id, key.Secret)
	}
}
