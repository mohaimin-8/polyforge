// Package redisopt applies the connection timeouts every PolyForge service
// wants from Redis.
//
// Both the limiter and the shared canary breaker are documented to degrade
// when Redis is unreachable, and both do -- but go-redis defaults to a 5 s
// dial and three retries, so the DIAL is what costs the request, not the
// fallback. A Redis blip therefore becomes a latency outage for every caller
// even though the fallback logic is correct.
//
// This lived in cmd/control-plane as an unexported helper. The ai-gateway
// parsed its own URL and never called anything like it, so the gateway kept
// go-redis's defaults -- the exact stall the control plane had already fixed,
// still live in the service that fronts every AI request. A shared default
// that only one of two callers applies is not a default; hence a package.
package redisopt

import (
	"os"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// The fallback is a local token bucket, so reaching it quickly costs almost
// nothing; waiting for it costs the request. These are aggressive by design,
// and a deployment that wants go-redis's defaults can set them explicitly in
// POLYFORGE_REDIS_URL (dial_timeout=, read_timeout=) or in the environment.
const (
	DefaultDialTimeout = 200 * time.Millisecond
	DefaultIOTimeout   = 200 * time.Millisecond
	DefaultMaxRetries  = 1
)

// Apply fills only what the URL left unset, so an explicit setting in
// POLYFORGE_REDIS_URL always wins.
func Apply(options *redis.Options) {
	if options == nil {
		return
	}
	if options.DialTimeout == 0 {
		options.DialTimeout = envDuration("POLYFORGE_REDIS_DIAL_TIMEOUT", DefaultDialTimeout)
	}
	if options.ReadTimeout == 0 {
		options.ReadTimeout = envDuration("POLYFORGE_REDIS_READ_TIMEOUT", DefaultIOTimeout)
	}
	if options.WriteTimeout == 0 {
		options.WriteTimeout = options.ReadTimeout
	}
	// go-redis reads 0 as "use my default of 3" and a negative as "none", so
	// 0 here is the case that has to be overridden.
	if options.MaxRetries == 0 {
		options.MaxRetries = envInt("POLYFORGE_REDIS_MAX_RETRIES", DefaultMaxRetries)
	}
}

func envDuration(name string, fallback time.Duration) time.Duration {
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	parsed, err := time.ParseDuration(value)
	if err != nil || parsed <= 0 {
		return fallback
	}
	return parsed
}

func envInt(name string, fallback int) int {
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		return fallback
	}
	return parsed
}
