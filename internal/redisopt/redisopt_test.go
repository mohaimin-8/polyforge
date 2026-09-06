package redisopt

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// Every service that builds a Redis client must apply these timeouts. The
// defaults lived in cmd/control-plane as unexported constants for eight
// sessions, and the ai-gateway -- which parses its own POLYFORGE_REDIS_URL --
// never applied anything, so it kept go-redis's 5 s dial and three retries on
// the path that fronts every AI request. Nothing caught that, because a
// missing call looks like nothing at all.
func TestEveryServiceThatDialsRedisAppliesTheseTimeouts(t *testing.T) {
	mains, err := filepath.Glob(filepath.Join("..", "..", "cmd", "*", "main.go"))
	if err != nil || len(mains) == 0 {
		t.Fatalf("no cmd/*/main.go found: %v", err)
	}
	checked := 0
	for _, path := range mains {
		source, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		text := string(source)
		if !strings.Contains(text, "redis.ParseURL") {
			continue
		}
		checked++
		if !strings.Contains(text, "redisopt.Apply") {
			t.Errorf("%s builds a Redis client but never calls redisopt.Apply, "+
				"so it keeps go-redis's 5s dial and a blip stalls every request", path)
		}
	}
	if checked < 2 {
		t.Fatalf("expected at least 2 services dialling Redis, found %d", checked)
	}
}

func TestAnExplicitSettingInTheURLWins(t *testing.T) {
	options := &redis.Options{DialTimeout: 3 * time.Second}
	Apply(options)
	if options.DialTimeout != 3*time.Second {
		t.Errorf("DialTimeout = %v, want the explicit 3s left untouched", options.DialTimeout)
	}
}

func TestNilIsNotADereference(t *testing.T) {
	Apply(nil)
}
