// Package egress builds the HTTP client every outbound call in the AI data
// plane uses (model providers, the agent's search tool, the embeddings
// endpoint). It is a leaf package so that both the gateway and the embedder
// can share one guard without an import cycle.
package egress

import (
	"fmt"
	"net/http"
	"time"
)

// Client hardens the process against SSRF-by-redirect: a request goes to an
// operator-configured host, but nothing stops a compromised or malicious
// upstream from answering with a 302 to an internal address -- cloud metadata
// at 169.254.169.254, a sibling service, localhost. Go's default client
// follows up to 10 redirects automatically, so that reply would be fetched.
// This client refuses to follow ANY redirect: providers speak a
// request/response protocol and have no legitimate need for one, so a 3xx is
// treated as the upstream's final answer rather than a new fetch. Combined
// with the existing containment (provider base URLs are operator config, the
// search host is hardcoded and the query URL-encoded), this closes the
// redirect vector as defense in depth.
func Client(timeout time.Duration) *http.Client {
	return &http.Client{
		Timeout: timeout,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return fmt.Errorf("egress guard: refusing redirect to %s", req.URL.Host)
		},
	}
}
