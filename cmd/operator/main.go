// Command operator runs the PolyForge Kubernetes operator: it reconciles the
// Tenant/WorkloadProfile/Policy/Budget CRDs and (from W31) drives the JCAC
// planner loop. State lives entirely in the API server — the operator can be
// killed at any point and resumes from etcd with no in-memory drift.
package main

import (
	"context"
	"flag"
	"log/slog"
	"os"
	"strconv"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"

	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	"polyforge/internal/events"
	pfv1alpha1 "polyforge/internal/operator/api/v1alpha1"
	"polyforge/internal/operator/controllers"
	"polyforge/internal/operator/planner"
)

var scheme = runtime.NewScheme()

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(pfv1alpha1.AddToScheme(scheme))
}

// envInt32 reads a positive int32 from the environment, exiting on a value
// that does not parse: a typo silently falling back to the default would
// run the planner against the wrong capacity ceiling.
func envInt32(log *slog.Logger, name string, fallback int32) int32 {
	raw := os.Getenv(name)
	if raw == "" {
		return fallback
	}
	v, err := strconv.ParseInt(raw, 10, 32)
	if err != nil || v <= 0 {
		log.Error("invalid integer environment variable", "name", name, "value", raw)
		os.Exit(1)
	}
	return int32(v)
}

func main() {
	var metricsAddr, probeAddr string
	var enableLeaderElection bool
	flag.StringVar(&metricsAddr, "metrics-bind-address", ":8080", "metrics endpoint address")
	flag.StringVar(&probeAddr, "health-probe-bind-address", ":8081", "liveness/readiness probe address")
	flag.BoolVar(&enableLeaderElection, "leader-elect", false,
		"enable leader election so only one operator replica reconciles")
	flag.Parse()

	ctrl.SetLogger(zap.New(zap.UseDevMode(false)))
	log := slog.New(slog.NewTextHandler(os.Stderr, nil))

	// Spans are always created (trace IDs correlate logs even standalone);
	// they only leave the process when an OTLP endpoint is configured.
	tracerOptions := []sdktrace.TracerProviderOption{
		sdktrace.WithResource(resource.NewSchemaless(
			attribute.String("service.name", "polyforge-operator"),
		)),
	}
	if endpoint := os.Getenv("POLYFORGE_OTLP_ENDPOINT"); endpoint != "" {
		exporter, err := otlptracehttp.New(context.Background(),
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
	tracerProvider := sdktrace.NewTracerProvider(tracerOptions...)
	otel.SetTracerProvider(tracerProvider)
	defer func() {
		if err := tracerProvider.Shutdown(context.Background()); err != nil {
			log.Error("tracer provider shutdown", "error", err)
		}
	}()

	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
		Scheme:                 scheme,
		Metrics:                metricsserver.Options{BindAddress: metricsAddr},
		HealthProbeBindAddress: probeAddr,
		LeaderElection:         enableLeaderElection,
		LeaderElectionID:       "operator.polyforge.io",
	})
	if err != nil {
		log.Error("create manager", "error", err)
		os.Exit(1)
	}

	if err := (&controllers.TenantReconciler{
		Client: mgr.GetClient(),
		Scheme: mgr.GetScheme(),
	}).SetupWithManager(mgr); err != nil {
		log.Error("setup tenant controller", "error", err)
		os.Exit(1)
	}
	policyReconciler := &controllers.PolicyReconciler{Client: mgr.GetClient()}
	// The gateway knob push is opt-in like the JCAC loop: without a gateway
	// URL the Policy's cache/tier levers actuate on the ConfigMap only (the
	// pre-B1 behavior); with it they reach the live data plane and the
	// Applied condition covers them.
	if gatewayURL := os.Getenv("POLYFORGE_GATEWAY_ADMIN_URL"); gatewayURL != "" {
		policyReconciler.GatewayKnobs = controllers.NewHTTPGatewayKnobs(
			gatewayURL, os.Getenv("POLYFORGE_GATEWAY_ADMIN_KEY"))
		log.Info("gateway knob push enabled", "gateway", gatewayURL)
	}
	if err := policyReconciler.SetupWithManager(mgr); err != nil {
		log.Error("setup policy controller", "error", err)
		os.Exit(1)
	}

	// The JCAC loop is opt-in: it needs both a planner and a demand
	// signal. Without either, the operator still reconciles CRDs — plans
	// just come from humans editing Policy objects.
	plannerURL := os.Getenv("POLYFORGE_PLANNER_URL")
	featuresURL := os.Getenv("POLYFORGE_FEATURES_URL")
	if plannerURL != "" && featuresURL != "" {
		demands := planner.NewFeatureDemandSource(featuresURL, os.Getenv("POLYFORGE_FEATURES_TOKEN"))
		// The admin key authenticates the operator as platform
		// infrastructure: it reads every managed tenant's features, which
		// a tenant-scoped bearer token cannot (Phase 7 jcac live arm).
		demands.AdminKey = os.Getenv("POLYFORGE_FEATURES_ADMIN_KEY")
		runner := &controllers.PlanRunner{
			Client:  mgr.GetClient(),
			Planner: planner.NewHTTPClient(plannerURL, 3*time.Second),
			Demands: demands,
			Log:     log,
			Weights: planner.Weights{Alpha: 1, Beta: 2, Gamma: 0.5},
			// Cluster-wide ceilings; the eval mirrors the sim's cluster
			// sizes through these so live and sim arms face the same
			// capacity bounds (a config typo must fail loudly, not
			// silently become a default).
			Limits: planner.Limits{
				CacheMB:  envInt32(log, "POLYFORGE_PLAN_LIMIT_CACHE_MB", 4096),
				Replicas: envInt32(log, "POLYFORGE_PLAN_LIMIT_REPLICAS", 60),
			},
		}
		if natsURL := os.Getenv("POLYFORGE_NATS_URL"); natsURL != "" {
			backbone, err := events.Connect(context.Background(), natsURL, events.BackboneConfig{})
			if err != nil {
				log.Error("connect NATS audit backbone", "error", err)
				os.Exit(1)
			}
			defer backbone.Close()
			runner.Audit = backbone
		}
		if err := mgr.Add(runner); err != nil {
			log.Error("add plan runner", "error", err)
			os.Exit(1)
		}
		log.Info("JCAC plan loop enabled", "planner", plannerURL, "features", featuresURL)
	} else {
		log.Info("JCAC plan loop disabled (set POLYFORGE_PLANNER_URL and POLYFORGE_FEATURES_URL)")
	}

	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		log.Error("add healthz check", "error", err)
		os.Exit(1)
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		log.Error("add readyz check", "error", err)
		os.Exit(1)
	}

	log.Info("starting polyforge-operator")
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		log.Error("manager exited", "error", err)
		os.Exit(1)
	}
}
