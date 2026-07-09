// Command replay drives PolyForge with a normalized workload trace (W25b).
//
//	replay -trace research/traces/out/lmsys_synth.csv.gz -mix mix.json -speed 2.0
//	replay -trace ... -dry-run          # print hashes, send nothing
//
// The mix file assigns trace tenants to PolyForge tenants:
//
//	{"targets":[{"tenant_id":"acme","api_key":"pf_...","weight":3},
//	            {"tenant_id":"globex","api_key":"pf_...","weight":1}]}
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"time"

	"polyforge/internal/research/replay"
)

func main() {
	tracePath := flag.String("trace", "", "normalized trace (.csv or .csv.gz)")
	mixPath := flag.String("mix", "", "tenant-mix JSON (required unless -dry-run)")
	baseURL := flag.String("url", "http://localhost:8080", "control-plane base URL")
	speed := flag.Float64("speed", 1.0, "speed multiplier (0.1 = 10x slower, 10 = 10x faster)")
	seed := flag.Uint64("seed", 42, "tenant-mapping seed")
	dryRun := flag.Bool("dry-run", false, "compute schedule and hashes without sending")
	flag.Parse()

	if *tracePath == "" {
		fmt.Fprintln(os.Stderr, "replay: -trace is required")
		os.Exit(2)
	}
	events, err := replay.Open(*tracePath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	mix := replay.Mix{Targets: []replay.MixTarget{{TenantID: "dry-run", Weight: 1}}}
	if *mixPath != "" {
		if mix, err = replay.LoadMix(*mixPath); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	} else if !*dryRun {
		fmt.Fprintln(os.Stderr, "replay: -mix is required unless -dry-run")
		os.Exit(2)
	}

	schedule, err := replay.Schedule(events, mix, *seed, *speed)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	duration := time.Duration(0)
	if len(schedule) > 0 {
		duration = schedule[len(schedule)-1].Due
	}
	fmt.Printf("trace: %d events, trace_hash %s\n", len(events), replay.TraceHash(events))
	fmt.Printf("schedule: seed %d, speed %.2fx, duration %s, schedule_hash %s\n",
		*seed, *speed, duration.Round(time.Millisecond), replay.ScheduleHash(schedule))
	if *dryRun {
		return
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	runner := &replay.Runner{}
	stats, err := runner.Run(ctx, schedule, &replay.HTTPSender{BaseURL: *baseURL})
	if err != nil {
		fmt.Fprintf(os.Stderr, "replay interrupted: %v\n", err)
	}
	fmt.Printf("sent %d, errors %d, max_lag %s\n", stats.Sent, stats.Errors, stats.MaxLag.Round(time.Millisecond))
	for tenant, count := range stats.Tenants {
		fmt.Printf("  %s: %d\n", tenant, count)
	}
	if stats.Errors > 0 {
		os.Exit(1)
	}
}
