package main

import (
	"math/rand"
	"testing"

	"polyforge/internal/classifier"
	"polyforge/internal/telemetry"
)

// Audit 2026-09-26 (MEDIUM): the rule baseline exists twice -- the live
// fallback (classifier.Classify, on raw telemetry windows) and this CLI's
// predictWithRules (on the feature vector, which cannot see the raw RPS
// spread and substitutes a coefficient-of-variation rule). The
// rules_w19_baseline row of research/results/model_selection.csv is scored
// with the adapter, so it was never shown to describe the live classifier.
// These tests re-draw the exact held-out corpus behind that row and measure
// both on it.

const (
	publishedSeed     = 1   // main's -seed default: held-out windows use seed+1
	publishedPerClass = 100 // main's -test-per-class default
)

// heldOutWindows re-draws the published held-out corpus as raw windows:
// SyntheticExamples draws one SyntheticWindow per example, in class order,
// from one generator (Features consumes no randomness), then shuffles with
// the same generator -- replayed here so the order matches too.
func heldOutWindows() ([][]telemetry.Event, []classifier.Label) {
	rng := rand.New(rand.NewSource(publishedSeed + 1))
	var windows [][]telemetry.Event
	var labels []classifier.Label
	for _, label := range classifier.Classes {
		for i := 0; i < publishedPerClass; i++ {
			windows = append(windows, classifier.SyntheticWindow(rng, label))
			labels = append(labels, label)
		}
	}
	rng.Shuffle(len(windows), func(i, j int) {
		windows[i], windows[j] = windows[j], windows[i]
		labels[i], labels[j] = labels[j], labels[i]
	})
	return windows, labels
}

func TestTheRedrawnCorpusIsThePublishedHeldOutSet(t *testing.T) {
	windows, labels := heldOutWindows()
	published := classifier.SyntheticExamples(rand.New(rand.NewSource(publishedSeed+1)), publishedPerClass)
	if len(published) != len(windows) {
		t.Fatalf("corpus sizes differ: %d vs %d", len(published), len(windows))
	}
	for i := range windows {
		if classifier.Features(windows[i]) != published[i].Features || labels[i] != published[i].Label {
			t.Fatalf("window %d is not the published held-out example", i)
		}
	}
}

func TestTheRuleAdapterAgainstTheLiveClassifier(t *testing.T) {
	windows, labels := heldOutWindows()
	var agree, liveRight, adapterRight int
	for i, w := range windows {
		live := classifier.Classify("t", w).Label
		adapted := predictWithRules(classifier.Features(w))
		if live == adapted {
			agree++
		}
		if live == labels[i] {
			liveRight++
		}
		if adapted == labels[i] {
			adapterRight++
		}
	}
	// Pinned counts on the 500-window corpus. The adapter reproduces the
	// published row (0.8880); the live classifier, on the same windows,
	// scores 0.8300 and agrees with the adapter on 94.2% of them -- the
	// published row overstates the live fallback by 5.8 points. A change to
	// either classifier moves these counts and must re-examine that row.
	if adapterRight != 444 {
		t.Errorf("adapter correct on %d of 500; the published row (0.8880) needs 444", adapterRight)
	}
	if liveRight != 415 || agree != 471 {
		t.Errorf("live classifier: %d correct, %d agreeing with the adapter; pinned 415 and 471 "+
			"(re-examine research/results/model_selection.csv's rules row)", liveRight, agree)
	}
}
