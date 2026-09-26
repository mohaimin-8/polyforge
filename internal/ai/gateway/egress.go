package gateway

import (
	"net/http"
	"time"

	"polyforge/internal/ai/egress"
)

// egressClient is the redirect-refusing client every outbound call in the
// gateway uses; the guard itself lives in internal/ai/egress so the
// embeddings client shares it (audit 2026-09-26: it did not).
func egressClient(timeout time.Duration) *http.Client {
	return egress.Client(timeout)
}
