// The AI gateway is PolyForge's second service (roadmap W17-W19): it fronts
// an OpenAI-compatible model host with a per-tenant semantic cache, serves
// tenant-scoped semantic search, and runs the tool-use agent loop. It
// shares the control plane's database so tenant API keys authenticate here
// unchanged and AI telemetry lands where the classifier reads it.
package main

import (
	"context"
	"fmt"
	"html"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"syscall"
	"time"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"

	"polyforge/internal/ai/agent"
	"polyforge/internal/ai/embed"
	"polyforge/internal/ai/gateway"
	sqlitestore "polyforge/internal/storage/sqlite"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	ctx := context.Background()

	dbPath := os.Getenv("POLYFORGE_DB_PATH")
	if dbPath == "" {
		dbPath = filepath.Join("data", "polyforge.db")
	}
	store, err := sqlitestore.Open(ctx, dbPath)
	if err != nil {
		log.Error("open SQLite", "path", dbPath, "error", err)
		os.Exit(1)
	}
	defer func() { _ = store.Close() }()

	// Embedder: local by default (deterministic, offline); OpenAI-compatible
	// when an endpoint is configured.
	var embedder embed.Embedder
	if base := os.Getenv("POLYFORGE_EMBED_BASE_URL"); base != "" {
		embedder = embed.NewOpenAI(base,
			os.Getenv("POLYFORGE_EMBED_API_KEY"),
			envOr("POLYFORGE_EMBED_MODEL", "text-embedding-3-small"),
			envInt("POLYFORGE_EMBED_DIMS", 1536))
	} else {
		embedder = embed.NewLocal(envInt("POLYFORGE_EMBED_DIMS", 256))
	}

	llmBase := os.Getenv("POLYFORGE_LLM_BASE_URL")
	if llmBase == "" {
		// Ollama's OpenAI-compatible endpoint is the local default.
		llmBase = "http://127.0.0.1:11434/v1"
	}
	provider := gateway.NewOpenAICompat(llmBase,
		os.Getenv("POLYFORGE_LLM_API_KEY"),
		envOr("POLYFORGE_LLM_MODEL", "llama3.2"))

	tracerOptions := []sdktrace.TracerProviderOption{
		sdktrace.WithResource(resource.NewSchemaless(
			attribute.String("service.name", "polyforge-ai-gateway"),
		)),
	}
	if endpoint := os.Getenv("POLYFORGE_OTLP_ENDPOINT"); endpoint != "" {
		exporter, err := otlptracehttp.New(ctx,
			otlptracehttp.WithEndpoint(endpoint),
			otlptracehttp.WithInsecure(),
		)
		if err != nil {
			log.Error("create OTLP trace exporter", "error", err)
			os.Exit(1)
		}
		tracerOptions = append(tracerOptions, sdktrace.WithBatcher(exporter))
	}
	tracerProvider := sdktrace.NewTracerProvider(tracerOptions...)
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = tracerProvider.Shutdown(shutdownCtx)
	}()

	runner := agent.NewRunner(provider, []agent.Tool{
		&agent.QueryTool{Repo: store},
		agent.CalcTool{},
		&agent.SearchTool{Search: duckDuckGoSearch},
	}, tracerProvider, agent.DefaultMaxSteps)

	server := &http.Server{
		Addr: envOr("POLYFORGE_AI_ADDR", ":8081"),
		Handler: gateway.NewServer(log, gateway.Config{
			Provider:  provider,
			Embedder:  embedder,
			Tenants:   store,
			Telemetry: store,
			Agent:     runner.GatewayFunc(),
			ModelTier: envOr("POLYFORGE_MODEL_TIER", "mid"),
		}).Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      5 * time.Minute, // streams stay open
		IdleTimeout:       60 * time.Second,
	}

	errs := make(chan error, 1)
	go func() {
		log.Info("starting polyforge ai-gateway", "addr", server.Addr, "llm_base_url", llmBase, "embedder", embedder.Model())
		errs <- server.ListenAndServe()
	}()

	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGINT, syscall.SIGTERM)
	select {
	case sig := <-signals:
		log.Info("shutdown requested", "signal", sig.String())
	case err := <-errs:
		if err != nil && err != http.ErrServerClosed {
			log.Error("server stopped", "error", err)
		}
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = server.Shutdown(shutdownCtx)
}

var ddgResultPattern = regexp.MustCompile(`<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>`)
var tagPattern = regexp.MustCompile(`<[^>]+>`)

// duckDuckGoSearch scrapes the HTML endpoint, which needs no API key. It is
// best-effort: agent tool failures surface to the model, not the user.
func duckDuckGoSearch(ctx context.Context, query string) ([]agent.SearchResult, error) {
	endpoint := "https://html.duckduckgo.com/html/?q=" + url.QueryEscape(query)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "polyforge-ai-gateway/0.1")
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("search returned %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
	if err != nil {
		return nil, err
	}
	matches := ddgResultPattern.FindAllStringSubmatch(string(body), 5)
	results := make([]agent.SearchResult, 0, len(matches))
	for _, m := range matches {
		results = append(results, agent.SearchResult{
			Title: html.UnescapeString(tagPattern.ReplaceAllString(m[2], "")),
			URL:   html.UnescapeString(m[1]),
		})
	}
	return results, nil
}

func envOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func envInt(name string, fallback int) int {
	if value := os.Getenv(name); value != "" {
		if parsed, err := strconv.Atoi(value); err == nil {
			return parsed
		}
	}
	return fallback
}
