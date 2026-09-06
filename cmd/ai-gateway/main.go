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

	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"

	"polyforge/internal/ai/agent"
	"polyforge/internal/ai/embed"
	"polyforge/internal/ai/gateway"
	"polyforge/internal/redisopt"
	"polyforge/internal/secrets"
	postgresstore "polyforge/internal/storage/postgres"
	sqlitestore "polyforge/internal/storage/sqlite"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

// gatewayStore is the slice of the shared store the gateway needs: API-key
// auth and telemetry. Both the SQLite and PostgreSQL stores satisfy it.
type gatewayStore interface {
	tenant.Repository
	telemetry.Repository
}

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	ctx := context.Background()

	// Same store contract as the control plane (and eval-export): shared
	// PostgreSQL when configured — required for the live eval plane, where
	// gateway telemetry must land in the store eval-export reads — SQLite
	// otherwise.
	var store gatewayStore
	if adminURL := os.Getenv("POLYFORGE_POSTGRES_ADMIN_URL"); adminURL != "" {
		pg, err := postgresstore.Open(ctx, postgresstore.Config{
			AdminURL: adminURL,
			AppURL:   os.Getenv("POLYFORGE_POSTGRES_APP_URL"),
		})
		if err != nil {
			log.Error("open PostgreSQL", "error", err)
			os.Exit(1)
		}
		defer pg.Close()
		store = pg
	} else {
		dbPath := os.Getenv("POLYFORGE_DB_PATH")
		if dbPath == "" {
			dbPath = filepath.Join("data", "polyforge.db")
		}
		sq, err := sqlitestore.Open(ctx, dbPath)
		if err != nil {
			log.Error("open SQLite", "path", dbPath, "error", err)
			os.Exit(1)
		}
		defer func() { _ = sq.Close() }()
		store = sq
	}

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
	// W23: provider credentials resolve through the secrets chain (Vault
	// Agent file -> Vault -> env). Local Ollama needs no key, so a resolution
	// miss is not an error here.
	secretSource := secrets.FromEnvironment()
	llmKey, _ := secretSource.Secret(ctx, "POLYFORGE_LLM_API_KEY")
	provider := gateway.NewOpenAICompat(llmBase, llmKey,
		envOr("POLYFORGE_LLM_MODEL", "llama3.2"))

	// Application-level canary (roadmap W22): a second model host gets
	// POLYFORGE_CANARY_WEIGHT percent of traffic and auto-rolls-back on an
	// error spike. The in-mesh pod-level twin is deploy/linkerd/.
	var chatProvider gateway.Provider = provider
	if canaryBase := os.Getenv("POLYFORGE_CANARY_LLM_BASE_URL"); canaryBase != "" {
		weight := envInt("POLYFORGE_CANARY_WEIGHT", 5)
		canaryModel := envOr("POLYFORGE_CANARY_LLM_MODEL", envOr("POLYFORGE_LLM_MODEL", "llama3.2"))
		canaryProvider := gateway.NewOpenAICompat(canaryBase,
			os.Getenv("POLYFORGE_CANARY_LLM_API_KEY"), canaryModel)
		// With Redis the rollback breaker is shared across gateway replicas,
		// so a bad canary rolls back fleet-wide; without it the breaker is
		// in-process, correct for a single replica. Keyed by canary identity
		// so distinct canary rollouts don't share a trip decision.
		if redisURL := os.Getenv("POLYFORGE_REDIS_URL"); redisURL != "" {
			options, err := redis.ParseURL(redisURL)
			if err != nil {
				log.Error("parse POLYFORGE_REDIS_URL", "error", err)
				os.Exit(1)
			}
			// Without this the gateway kept go-redis's 5 s dial and three
			// retries, so an unreachable Redis stalled every request that
			// touched the shared breaker before the in-process fallback could
			// engage. The control plane fixed that in session 35; this service
			// never got it.
			redisopt.Apply(options)
			client := redis.NewClient(options)
			defer func() { _ = client.Close() }()
			chatProvider = gateway.NewSharedCanaryProvider(provider, canaryProvider, weight, client, canaryModel)
			log.Info("canary active (shared breaker)", "base_url", canaryBase, "weight_percent", weight, "redis", options.Addr)
		} else {
			chatProvider = gateway.NewCanaryProvider(provider, canaryProvider, weight)
			log.Info("canary active", "base_url", canaryBase, "weight_percent", weight)
		}
	}

	// Multi-backend routing (roadmap W20): a JSON policy file turns the
	// single provider into a routed fleet — local Ollama for free tenants,
	// fast/quality cloud backends for paid plans, budget-capped.
	var router *gateway.Router
	if path := os.Getenv("POLYFORGE_ROUTING_CONFIG"); path != "" {
		file, err := gateway.LoadRouterFile(path)
		if err != nil {
			log.Error("load routing config", "path", path, "error", err)
			os.Exit(1)
		}
		router, err = file.Build()
		if err != nil {
			log.Error("build router", "path", path, "error", err)
			os.Exit(1)
		}
		log.Info("routing policy active", "backends", len(file.Backends), "rules", len(file.Rules), "default", file.Default)
	}

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

	// Tier backends (PREREG_WAVE4_LIVE_PLANE.md §Substrate 2): a JSON map
	// tier -> backend makes the per-tenant tier knob a real routing lever.
	tierProviders, err := gateway.BuildTierProviders(os.Getenv("POLYFORGE_TIER_BACKENDS"))
	if err != nil {
		log.Error("parse POLYFORGE_TIER_BACKENDS", "error", err)
		os.Exit(1)
	}
	if len(tierProviders) > 0 {
		log.Info("tier backends active", "tiers", len(tierProviders))
	}

	server := &http.Server{
		Addr: envOr("POLYFORGE_AI_ADDR", ":8081"),
		Handler: gateway.NewServer(log, gateway.Config{
			Provider:      chatProvider,
			Router:        router,
			Embedder:      embedder,
			Tenants:       store,
			Telemetry:     store,
			Agent:         runner.GatewayFunc(),
			ModelTier:     envOr("POLYFORGE_MODEL_TIER", "mid"),
			CacheShared:   os.Getenv("POLYFORGE_CACHE_SHARED") == "1",
			TierProviders: tierProviders,
			AdminKey:      os.Getenv("POLYFORGE_ADMIN_KEY"),
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
	// Refuse redirects: the search host is fixed, and a 3xx to another host
	// (an internal service, cloud metadata) must not be followed. Mirrors the
	// gateway providers' egress guard.
	client := &http.Client{
		Timeout: 15 * time.Second,
		CheckRedirect: func(r *http.Request, _ []*http.Request) error {
			return fmt.Errorf("search egress guard: refusing redirect to %s", r.URL.Host)
		},
	}
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
