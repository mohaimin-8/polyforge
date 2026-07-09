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

	"polyforge/internal/platform"
	postgresstore "polyforge/internal/storage/postgres"
	sqlitestore "polyforge/internal/storage/sqlite"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

func main() {
	// -healthcheck probes the running server and exits 0/1. It exists for
	// container health checks: the distroless production image has no shell
	// or curl, so the binary is the only thing that can perform the probe.
	if len(os.Args) > 1 && os.Args[1] == "-healthcheck" {
		os.Exit(healthcheck())
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

	adminKey := os.Getenv("POLYFORGE_ADMIN_KEY")
	if adminKey == "" {
		log.Error("POLYFORGE_ADMIN_KEY is required")
		os.Exit(1)
	}
	rateLimit := platform.RateLimitConfig{
		RequestsPerMinute: envInt("POLYFORGE_RATE_LIMIT_RPM", 600),
		Burst:             envInt("POLYFORGE_RATE_LIMIT_BURST", 60),
	}

	server := &http.Server{
		Addr:              ":8080",
		Handler:           platform.NewServer(log, tenantRepository, telemetryRepository, platform.Config{AdminKey: adminKey, RateLimit: rateLimit}).Handler(),
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
