package platform

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"strings"

	"polyforge/internal/idempotency"
	"polyforge/internal/tenant"
)

// idempotency makes mutating requests repeat-safe (roadmap W14): when the
// client sends an Idempotency-Key header, the first request claims the key,
// its response is cached, and a retry replays that response instead of
// executing the write again. The key is scoped to the credential and to the
// request body's digest, so one tenant can never replay another tenant's
// response and a reused key with a different payload executes as a distinct
// request rather than silently returning the wrong cached result.
func (s *Server) idempotency(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := strings.TrimSpace(r.Header.Get("Idempotency-Key"))
		if key == "" || !mutatingMethod(r.Method) {
			next.ServeHTTP(w, r)
			return
		}
		if len(key) > 200 {
			s.writeError(w, r, http.StatusBadRequest, fmt.Errorf("Idempotency-Key must be at most 200 characters"))
			return
		}

		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxRequestBodyBytes))
		if err != nil {
			s.writeError(w, r, http.StatusRequestEntityTooLarge, fmt.Errorf("request body exceeds %d bytes", maxRequestBodyBytes))
			return
		}
		r.Body = io.NopCloser(bytes.NewReader(body))

		digest := idempotencyDigest(r, key, body)
		cached, inFlight, err := s.idempotencyStore.Begin(r.Context(), digest)
		if err != nil {
			// Fail open, mirroring the rate limiter: losing replay
			// protection is better than refusing all writes.
			s.log.Warn("idempotency store unavailable; executing request",
				"request_id", requestID(r.Context()), "error", err)
			next.ServeHTTP(w, r)
			return
		}
		if inFlight {
			s.writeError(w, r, http.StatusConflict,
				fmt.Errorf("%w: a request with this Idempotency-Key is still in flight", tenant.ErrConflict))
			return
		}
		if cached != nil {
			w.Header().Set("Content-Type", cached.ContentType)
			w.Header().Set("Idempotency-Replayed", "true")
			w.WriteHeader(cached.Status)
			_, _ = w.Write(cached.Body)
			return
		}

		recorder := &bufferingWriter{ResponseWriter: w}
		next.ServeHTTP(recorder, r)
		if recorder.status == 0 {
			recorder.status = http.StatusOK
		}
		if recorder.status >= http.StatusInternalServerError {
			// Do not cache failures the client should retry.
			if err := s.idempotencyStore.Abandon(r.Context(), digest); err != nil {
				s.log.Warn("release idempotency key", "request_id", requestID(r.Context()), "error", err)
			}
			return
		}
		err = s.idempotencyStore.Complete(r.Context(), digest, idempotency.CachedResponse{
			Status:      recorder.status,
			ContentType: recorder.Header().Get("Content-Type"),
			Body:        recorder.body.Bytes(),
		})
		if err != nil {
			s.log.Warn("cache idempotent response", "request_id", requestID(r.Context()), "error", err)
		}
	})
}

func mutatingMethod(method string) bool {
	switch method {
	case http.MethodPost, http.MethodPatch, http.MethodPut, http.MethodDelete:
		return true
	}
	return false
}

func idempotencyDigest(r *http.Request, key string, body []byte) string {
	credential := strings.TrimSpace(r.Header.Get("X-PolyForge-API-Key"))
	if credential == "" {
		credential = bearerToken(r)
	}
	scope := "anon"
	if credential != "" {
		scope = secretFingerprint(credential)
	}
	bodySum := sha256.Sum256(body)
	sum := sha256.Sum256([]byte(strings.Join([]string{
		scope, r.Method, r.URL.Path, key, hex.EncodeToString(bodySum[:]),
	}, "\x00")))
	return hex.EncodeToString(sum[:])
}

type bufferingWriter struct {
	http.ResponseWriter
	status int
	body   bytes.Buffer
}

func (w *bufferingWriter) WriteHeader(status int) {
	if w.status != 0 {
		return
	}
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func (w *bufferingWriter) Write(value []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	w.body.Write(value)
	return w.ResponseWriter.Write(value)
}
