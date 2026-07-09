// llmbench measures TTFT, throughput, and cost across every backend in the
// routing config (roadmap W20). It produces the CSV cited as Table 1:
//
//	go run ./cmd/llmbench -config deploy/routing.json -runs 5 -out benchmarks/inference.csv
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"polyforge/internal/ai/bench"
	"polyforge/internal/ai/gateway"
)

func main() {
	configPath := flag.String("config", "deploy/routing.json", "routing config with the backends to benchmark")
	runs := flag.Int("runs", 3, "measured runs per prompt per backend")
	outPath := flag.String("out", "benchmarks/inference.csv", "CSV output path")
	timeout := flag.Duration("timeout", 10*time.Minute, "overall benchmark deadline")
	flag.Parse()

	if err := run(*configPath, *outPath, *runs, *timeout); err != nil {
		fmt.Fprintln(os.Stderr, "llmbench:", err)
		os.Exit(1)
	}
}

func run(configPath, outPath string, runs int, timeout time.Duration) error {
	file, err := gateway.LoadRouterFile(configPath)
	if err != nil {
		return err
	}
	router, err := file.Build()
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	backends := router.Backends()
	fmt.Printf("benchmarking %d backends, %d runs per prompt\n", len(backends), runs)
	results := bench.Run(ctx, backends, bench.DefaultPrompts(), runs)

	if err := os.MkdirAll(filepath.Dir(outPath), 0o755); err != nil {
		return err
	}
	out, err := os.Create(outPath)
	if err != nil {
		return err
	}
	defer func() { _ = out.Close() }()
	if err := bench.WriteCSV(out, results); err != nil {
		return err
	}

	fmt.Println()
	fmt.Println(bench.Markdown(results))
	fmt.Printf("raw data: %s (%d rows)\n", outPath, len(results))
	return nil
}
