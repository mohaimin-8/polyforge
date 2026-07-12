package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"

	"polyforge/internal/classifier"
	"polyforge/internal/classifier/online"
	"polyforge/internal/events"
	"polyforge/internal/idempotency"
	"polyforge/internal/limit"
	"polyforge/internal/platform"
	"polyforge/internal/secrets"
	postgresstore "polyforge/internal/storage/postgres"
	sqlitestore "polyforge/internal/storage/sqlite"
	"polyforge/internal/telemetry"
	"polyforge/internal/telemetry/analytics"
	"polyforge/internal/tenant"
)

func main() {
	// -healthcheck probes the running server and exits 0/1. It exists for
	// container health checks: the distroless production image has no shell
	// or curl, so the binary is the only thing that can perform the probe.
	if len(os.Args) > 1 && os.Args[1] == "-healthcheck" {
		os.Exit(healthcheck())
	}
	// eval-export dumps this pod's observed run metrics in the harness
	// schema (eval/README.md, cluster backend step 6). A subcommand, not a
	// server mode: the harness execs it inside the running pod.
	if len(os.Args) > 1 && os.Args[1] == "eval-export" {
		os.Exit(evalExport(os.Args[2:]))
	}

	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: envLogLevel("POLYFORGE_LOG_LEVEL", slog.LevelInfo)}))

	ctx := context.Background()
	var tenantRepository tenant.Repository
	var telemetryRepository telemetry.Repository
	var closeStore func()
	backend := "sqlite"

	postgresAdminURL := os.Getenv("POLYFORGE_POSTGRES_ADMIN_URL")
	if postgresAdminURL != "" {
		backend = "postgres"
		store, err := postgresstore.Open(ctx, postgresstore.Config{
			AdminURL: postgresAdminURL,
			AppURL:   os.Getenv("POLYFORGE_POSTGRES_APP_URL"),
		})
		if err != nil {
			log.Error("open PostgreSQL", "error", err)
			os.Exit(1)
		}
		if err := store.Migrate(ctx); err != nil {
			store.Close()
			log.Error("migrate PostgreSQL", "error", err)
			os.Exit(1)
		}
		tenantRepository = store
		telemetryRepository = store
		closeStore = store.Close
	} else {
		dbPath := os.Getenv("POLYFORGE_DB_PATH")
		if dbPath == "" {
			dbPath = filepath.Join("data", "polyforge.db")
		}
		if dir := filepath.Dir(dbPath); dir != "." {
			if err := os.MkdirAll(dir, 0o755); err != nil {
				log.Error("create database directory", "error", err)
				os.Exit(1)
			}
		}
		store, err := sqlitestore.Open(ctx, dbPath)
		if err != nil {
			log.Error("open SQLite", "path", dbPath, "error", err)
			os.Exit(1)
		}
		tenantRepository = store
		telemetryRepository = store
		closeStore = func() { _ = store.Close() }
	}
	defer closeStore()

	// With NATS configured, the outbox relay streams every audit event to
	// JetStream (ADR 0006); without it, events accumulate in the outbox and
	// publish on the first start that has a backbone. Nothing is lost either
	// way — that is the point of the outbox.
	var workloadPublisher online.Publisher
	if natsURL := os.Getenv("POLYFORGE_NATS_URL"); natsURL != "" {
		outbox, ok := tenantRepository.(events.Outbox)
		if !ok {
			log.Error("configured store does not expose an outbox")
			os.Exit(1)
		}
		backbone, err := events.Connect(ctx, natsURL, events.BackboneConfig{})
		if err != nil {
			log.Error("connect NATS JetStream", "error", err)
			os.Exit(1)
		}
		defer backbone.Close()
		workloadPublisher = backbone
		relayCtx, stopRelay := context.WithCancel(ctx)
		defer stopRelay()
		go events.NewRelay(outbox, backbone, log, time.Second).Run(relayCtx)
		log.Info("outbox relay started", "stream", events.StreamName, "nats_url", natsURL)
	}

	// W23: the admin key resolves through the secrets chain — Vault Agent
	// file, then Vault HTTP, then env — so rotating it in Vault requires no
	// binary change, and no restart when the agent rewrites the file.
	adminKey, err := secrets.FromEnvironment().Secret(ctx, "POLYFORGE_ADMIN_KEY")
	if err != nil {
		log.Error("POLYFORGE_ADMIN_KEY is required (env, secrets dir, or Vault)", "error", err)
		os.Exit(1)
	}
	rateLimit := platform.RateLimitConfig{
		RequestsPerMinute: envInt("POLYFORGE_RATE_LIMIT_RPM", 600),
		Burst:             envInt("POLYFORGE_RATE_LIMIT_BURST", 60),
	}

	// With Redis, rate limiting (sliding window) and idempotency replay are
	// shared across replicas; without it, both fall back to in-process
	// implementations, which is correct for a single instance.
	var requestLimiter platform.RequestLimiter
	var idempotencyStore idempotency.Store
	if redisURL := os.Getenv("POLYFORGE_REDIS_URL"); redisURL != "" {
		options, err := redis.ParseURL(redisURL)
		if err != nil {
			log.Error("parse POLYFORGE_REDIS_URL", "error", err)
			os.Exit(1)
		}
		client := redis.NewClient(options)
		defer func() { _ = client.Close() }()
		if rateLimit.RequestsPerMinute > 0 {
			requestLimiter = limit.NewSlidingWindow(client, rateLimit.RequestsPerMinute, time.Minute)
		}
		idempotencyStore = idempotency.NewRedisStore(client)
		log.Info("using Redis for rate limiting and idempotency", "addr", options.Addr)
	}

	// Spans are always created (trace IDs correlate logs even standalone);
	// they only leave the process when an OTLP endpoint is configured.
	tracerOptions := []sdktrace.TracerProviderOption{
		sdktrace.WithResource(resource.NewSchemaless(
			attribute.String("service.name", "polyforge-control-plane"),
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
		log.Info("exporting traces via OTLP/HTTP", "endpoint", endpoint)
	}
	// W25a: with ClickHouse configured, every accepted telemetry event is
	// mirrored to the analytical store through a bounded batcher (ADR 0011).
	// The mirror is best-effort by design; the transactional repository
	// remains the source of truth for the feature API.
	var analyticsEnqueuer platform.AnalyticsEnqueuer
	if clickhouseURL := os.Getenv("POLYFORGE_CLICKHOUSE_URL"); clickhouseURL != "" {
		sink, err := analytics.NewClickHouseSink(analytics.ClickHouseConfig{URL: clickhouseURL})
		if err != nil {
			log.Error("configure ClickHouse sink", "error", err)
			os.Exit(1)
		}
		batcher := analytics.NewBatcher(sink, log, analytics.BatcherConfig{})
		defer func() {
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := batcher.Close(shutdownCtx); err != nil {
				log.Error("analytics batcher shutdown", "error", err, "dropped", batcher.Dropped())
			}
		}()
		analyticsEnqueuer = batcher
		log.Info("mirroring telemetry to ClickHouse")
	}

	// W27: the online classifier polls every tenant's recent telemetry,
	// predicts the workload class, publishes label changes to NATS when
	// configured, and tracks feature drift with PSI. The trained W26
	// artifact is used when present; otherwise the W19 rules keep the
	// loop useful out of the box.
	var predictor online.Predictor = online.RulePredictor{}
	modelPath := envDefault("POLYFORGE_CLASSIFIER_MODEL", filepath.Join("artifacts", "classifier-model.json"))
	if model, err := classifier.LoadModel(modelPath); err == nil {
		predictor = model
		log.Info("online classifier using trained model", "path", modelPath)
	} else if !os.IsNotExist(err) {
		log.Error("load classifier model", "path", modelPath, "error", err)
		os.Exit(1)
	} else {
		log.Info("no classifier model artifact; online classifier using rule baseline", "path", modelPath)
	}
	var driftDetector *classifier.DriftDetector
	referencePath := envDefault("POLYFORGE_CLASSIFIER_REFERENCE", filepath.Join("artifacts", "classifier-reference.json"))
	if detector, err := classifier.LoadDriftDetector(referencePath); err == nil {
		driftDetector = detector
	} else if !os.IsNotExist(err) {
		log.Error("load drift reference", "path", referencePath, "error", err)
		os.Exit(1)
	}
	workloadRegistry := online.NewRegistry(0)
	poller := online.NewPoller(tenantRepository, telemetryRepository, predictor, driftDetector,
		workloadPublisher, workloadRegistry, log, online.Config{
			Interval: time.Duration(envInt("POLYFORGE_CLASSIFIER_INTERVAL_SECONDS", 10)) * time.Second,
		})
	pollCtx, stopPoller := context.WithCancel(ctx)
	defer stopPoller()
	go poller.Run(pollCtx)

	tracerProvider := sdktrace.NewTracerProvider(tracerOptions...)
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := tracerProvider.Shutdown(shutdownCtx); err != nil {
			log.Error("tracer provider shutdown", "error", err)
		}
	}()

	server := &http.Server{
		Addr: ":8080",
		Handler: platform.NewServer(log, tenantRepository, telemetryRepository, platform.Config{
			AdminKey:         adminKey,
			RateLimit:        rateLimit,
			Limiter:          requestLimiter,
			IdempotencyStore: idempotencyStore,
			TracerProvider:   tracerProvider,
			Analytics:        analyticsEnqueuer,
			Workloads:        workloadRegistry,
		}).Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	errs := make(chan error, 1)
	go func() {
		log.Info("starting polyforge control-plane", "addr", server.Addr, "database_backend", backend)
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

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		log.Error("graceful shutdown failed", "error", err)
	}
}

func healthcheck() int {
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://127.0.0.1:8080/healthz")
	if err != nil {
		return 1
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}

func envDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
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

// envLogLevel reads a slog level name (debug, info, warn, error) from the
// environment. Per-request access logs are emitted at info, so warn is the
// right setting for load benchmarks where logging would dominate the cost.
func envLogLevel(name string, fallback slog.Level) slog.Level {
	var level slog.Level
	if err := level.UnmarshalText([]byte(os.Getenv(name))); err != nil {
		return fallback
	}
	return level
}
