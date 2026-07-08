package platform

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"time"
)

type traceContext struct {
	TraceID      string
	SpanID       string
	ParentSpanID string
	Flags        string
	Remote       bool
}

func newTraceContext(header string) traceContext {
	if parent, ok := parseTraceParent(header); ok {
		return traceContext{
			TraceID:      parent.TraceID,
			SpanID:       randomNonZeroHex(8),
			ParentSpanID: parent.SpanID,
			Flags:        parent.Flags,
			Remote:       true,
		}
	}
	return traceContext{
		TraceID: randomNonZeroHex(16),
		SpanID:  randomNonZeroHex(8),
		Flags:   "01",
	}
}

func parseTraceParent(header string) (traceContext, bool) {
	parts := strings.Split(strings.TrimSpace(header), "-")
	if len(parts) != 4 {
		return traceContext{}, false
	}
	version := strings.ToLower(parts[0])
	traceID := strings.ToLower(parts[1])
	spanID := strings.ToLower(parts[2])
	flags := strings.ToLower(parts[3])
	if version != "00" || !validTraceHex(traceID, 32) || !validTraceHex(spanID, 16) || !validTraceHex(flags, 2) {
		return traceContext{}, false
	}
	if allZero(traceID) || allZero(spanID) {
		return traceContext{}, false
	}
	return traceContext{TraceID: traceID, SpanID: spanID, Flags: flags}, true
}

func (t traceContext) TraceParent() string {
	return "00-" + t.TraceID + "-" + t.SpanID + "-" + t.Flags
}

func randomNonZeroHex(bytes int) string {
	for {
		value := make([]byte, bytes)
		if _, err := rand.Read(value); err != nil {
			sum := sha256.Sum256([]byte(time.Now().Format(time.RFC3339Nano)))
			return hex.EncodeToString(sum[:bytes])
		}
		encoded := hex.EncodeToString(value)
		if !allZero(encoded) {
			return encoded
		}
	}
}

func validTraceHex(value string, length int) bool {
	if len(value) != length {
		return false
	}
	for _, r := range value {
		if (r < '0' || r > '9') && (r < 'a' || r > 'f') {
			return false
		}
	}
	return true
}

func allZero(value string) bool {
	for _, r := range value {
		if r != '0' {
			return false
		}
	}
	return true
}
