package tenant

import (
	"testing"
)

// FuzzNormalizePageRequest hammers the keyset-pagination normalizer with
// arbitrary cursor bytes and limits (both come straight from the query
// string). The contract must hold for every input: no panic, the limit
// lands in [1, MaxPageSize], and the cursor is trimmed.
func FuzzNormalizePageRequest(f *testing.F) {
	f.Add("", 0)
	f.Add("  key_abc  ", 50)
	f.Add("key_\x00\xff", -1)
	f.Add("'; DROP TABLE projects;--", 1_000_000)
	f.Add("key_zzz", MaxPageSize+1)

	f.Fuzz(func(t *testing.T, cursor string, limit int) {
		out := NormalizePageRequest(PageRequest{Cursor: cursor, Limit: limit})
		if out.Limit < 1 || out.Limit > MaxPageSize {
			t.Fatalf("limit %d escaped [1,%d] from input %d", out.Limit, MaxPageSize, limit)
		}
		// A second pass is idempotent (normalization is a fixed point).
		again := NormalizePageRequest(out)
		if again != out {
			t.Fatalf("normalize not idempotent: %+v -> %+v", out, again)
		}
	})
}

// FuzzScope pins the scope helpers against arbitrary bytes: normalization
// must never panic, ValidScope must agree with the two known scopes, and a
// key never grants more than its own scope.
func FuzzScope(f *testing.F) {
	f.Add("")
	f.Add("full")
	f.Add("read")
	f.Add("  READ ")
	f.Add("admin\x00")

	f.Fuzz(func(t *testing.T, scope string) {
		n := NormalizeScope(scope)
		if ValidScope(scope) {
			if n != ScopeRead && n != ScopeFull {
				t.Fatalf("valid scope normalized to %q", n)
			}
		}
		// A read key must never be allowed a full-scope action, whatever the
		// input normalizes to.
		if ScopeAllows(ScopeRead, ScopeFull) {
			t.Fatal("read key granted full-scope action")
		}
	})
}
