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

	pfv1alpha1 "polyforge/internal/operator/api/v1alpha1"
	"polyforge/internal/operator/controllers"
)

var scheme = runtime.NewScheme()

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(pfv1alpha1.AddToScheme(scheme))
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
	if err := (&controllers.PolicyReconciler{
		Client: mgr.GetClient(),
	}).SetupWithManager(mgr); err != nil {
		log.Error("setup policy controller", "error", err)
		os.Exit(1)
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
