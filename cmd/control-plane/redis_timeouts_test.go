package main

import (
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// The defect these pin: redis.ParseURL leaves the timeout fields zero, and
// redis.NewClient then fills zeros with 5s dial / 3s read / 3 retries. On a
// Redis outage that blocks the rate-limit hot path for seconds per request
// before server.go's (correct) local-bucket fallback can engage, turning an
// outage into a latency outage for every caller.

func TestApplyRedisTimeoutsFillsUnsetFields(t *testing.T) {
	options, err := redis.ParseURL("redis://localhost:6379/0")
	if err != nil {
		t.Fatalf("ParseURL: %v", err)
	}
	if options.DialTimeout != 0 {
		t.Fatalf("precondition changed: ParseURL now sets DialTimeout=%v; this "+
			"test exists because it does not", options.DialTimeout)
	}

	applyRedisTimeouts(options)

	if options.DialTimeout != defaultRedisDialTimeout {
		t.Errorf("DialTimeout = %v, want %v", options.DialTimeout, defaultRedisDialTimeout)
	}
	if options.ReadTimeout != defaultRedisIOTimeout {
		t.Errorf("ReadTimeout = %v, want %v", options.ReadTimeout, defaultRedisIOTimeout)
	}
	if options.WriteTimeout != options.ReadTimeout {
		t.Errorf("WriteTimeout = %v, want it to track ReadTimeout %v",
			options.WriteTimeout, options.ReadTimeout)
	}
	if options.MaxRetries != defaultRedisMaxRetries {
		t.Errorf("MaxRetries = %d, want %d", options.MaxRetries, defaultRedisMaxRetries)
	}
}

func TestApplyRedisTimeoutsIsWellUnderGoRedisDefaults(t *testing.T) {
	// The point is not the exact number, it is the order of magnitude: the
	// fallback must be reached in milliseconds, not in the 5s go-redis would
	// otherwise spend dialling a host that is not there.
	options, _ := redis.ParseURL("redis://localhost:6379/0")
	applyRedisTimeouts(options)
	if options.DialTimeout >= time.Second {
		t.Errorf("DialTimeout %v does not fail fast; the whole point is to reach "+
			"the local fallback before the caller notices", options.DialTimeout)
	}
	worst := options.DialTimeout * time.Duration(options.MaxRetries+1)
	if worst >= 2*time.Second {
		t.Errorf("worst-case dial budget %v (timeout %v x %d attempts) is still "+
			"a visible stall on every request during an outage",
			worst, options.DialTimeout, options.MaxRetries+1)
	}
}

func TestApplyRedisTimeoutsDoesNotOverrideExplicitURLSettings(t *testing.T) {
	// An operator who states a timeout in the URL means it. Filling only the
	// zero fields is what keeps this a default rather than a policy.
	options, err := redis.ParseURL(
		"redis://localhost:6379/0?dial_timeout=7s&read_timeout=11s&max_retries=5")
	if err != nil {
		t.Fatalf("ParseURL: %v", err)
	}
	applyRedisTimeouts(options)

	if options.DialTimeout != 7*time.Second {
		t.Errorf("DialTimeout = %v, want the URL's 7s", options.DialTimeout)
	}
	if options.ReadTimeout != 11*time.Second {
		t.Errorf("ReadTimeout = %v, want the URL's 11s", options.ReadTimeout)
	}
	if options.MaxRetries != 5 {
		t.Errorf("MaxRetries = %d, want the URL's 5", options.MaxRetries)
	}
}

func TestApplyRedisTimeoutsReadsTheEnvironment(t *testing.T) {
	t.Setenv("POLYFORGE_REDIS_DIAL_TIMEOUT", "50ms")
	t.Setenv("POLYFORGE_REDIS_MAX_RETRIES", "0")
	options, _ := redis.ParseURL("redis://localhost:6379/0")
	applyRedisTimeouts(options)

	if options.DialTimeout != 50*time.Millisecond {
		t.Errorf("DialTimeout = %v, want 50ms from the environment", options.DialTimeout)
	}
	if options.MaxRetries != 0 {
		t.Errorf("MaxRetries = %d, want the environment's 0", options.MaxRetries)
	}
}

func TestApplyRedisTimeoutsIgnoresGarbageEnvironment(t *testing.T) {
	// A typo in a deployment manifest must not silently restore the 5s stall.
	t.Setenv("POLYFORGE_REDIS_DIAL_TIMEOUT", "not-a-duration")
	options, _ := redis.ParseURL("redis://localhost:6379/0")
	applyRedisTimeouts(options)

	if options.DialTimeout != defaultRedisDialTimeout {
		t.Errorf("DialTimeout = %v, want the safe default %v",
			options.DialTimeout, defaultRedisDialTimeout)
	}
}

func TestApplyRedisTimeoutsHandlesNil(t *testing.T) {
	applyRedisTimeouts(nil) // must not panic
}
