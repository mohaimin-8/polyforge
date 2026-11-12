//go:build cachereplay

// Command gocheck produces the Go-side evidence for research/cachereplay
// (programme 2, WP6): traces of the real eviction policies and of the real
// semantic cache on seeded random scripts, which the Python mirror must
// reproduce, and the heap bytes one cache entry occupies. The build tag keeps
// it out of normal builds, tests and vet:
//
//	go run -tags cachereplay ./research/cachereplay/gocheck parity > research/cachereplay/testdata/go_parity.json
//	go run -tags cachereplay ./research/cachereplay/gocheck mem -lengths <csv of key_bytes,completion_bytes>
package main

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"math"
	"math/rand"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"

	"polyforge/internal/ai/embed"
	"polyforge/internal/ai/gateway"
	"polyforge/internal/ai/gateway/eviction"
)

var base = time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: gocheck parity | mem -lengths file.csv")
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "parity":
		err = runParity()
	case "mem":
		err = runMem(os.Args[2:])
	default:
		err = fmt.Errorf("unknown mode %q", os.Args[1])
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "gocheck:", err)
		os.Exit(1)
	}
}

// ---- policy parity ----------------------------------------------------------

// PolicyOp is one call on an eviction.Policy at virtual time T (seconds after
// base, rounded to the nanosecond as Go stores it). For "evict" the Go policy's
// Victim is recorded and then removed, as evictWhileOverLocked does.
type PolicyOp struct {
	Op      string  `json:"op"`
	ID      string  `json:"id,omitempty"`
	T       float64 `json:"t"`
	Cost    float64 `json:"cost,omitempty"`
	Density float64 `json:"density,omitempty"`
	Victim  string  `json:"victim,omitempty"`
}

type PolicyScript struct {
	Policy  string     `json:"policy"`
	Seed    int64      `json:"seed"`
	MeanGap float64    `json:"mean_gap_s"`
	Ops     []PolicyOp `json:"ops"`
}

func newPolicy(name string) eviction.Policy {
	if name == "lru" {
		return eviction.NewLRU()
	}
	return eviction.NewCostAware(eviction.CostAwareConfig{})
}

func policyScript(name string, seed int64, meanGap float64, nOps int) PolicyScript {
	rng := rand.New(rand.NewSource(seed))
	policy := newPolicy(name)
	script := PolicyScript{Policy: name, Seed: seed, MeanGap: meanGap}
	var resident []string
	t, next := 0.0, 0
	for i := 0; i < nOps; i++ {
		t += rng.ExpFloat64() * meanGap
		ns := int64(t * 1e9)
		now := base.Add(time.Duration(ns))
		op := PolicyOp{T: float64(ns) / 1e9}
		r := rng.Float64()
		switch {
		case len(resident) < 3 || r < 0.40:
			op.Op, op.ID = "store", fmt.Sprintf("e%05d", next)
			next++
			if len(resident) > 0 && r < 0.03 { // re-store a resident ID (Upsert)
				op.ID = resident[rng.Intn(len(resident))]
			} else {
				resident = append(resident, op.ID)
			}
			op.Cost = math.Exp(math.Log(1e-6) + rng.Float64()*math.Log(1e3))
			op.Density = rng.Float64()
			policy.OnStore(eviction.Entry{ID: op.ID, RecomputeCostUSD: op.Cost,
				EmbeddingDensity: op.Density, StoredAt: now}, now)
		case r < 0.75:
			op.Op, op.ID = "hit", resident[rng.Intn(len(resident))]
			policy.OnHit(op.ID, now)
		case r < 0.95:
			op.Op = "evict"
			victim, _ := policy.Victim(now)
			policy.OnRemove(victim)
			op.Victim = victim
			resident = without(resident, victim)
		default:
			op.Op, op.ID = "remove", resident[rng.Intn(len(resident))]
			policy.OnRemove(op.ID)
			resident = without(resident, op.ID)
		}
		script.Ops = append(script.Ops, op)
	}
	return script
}

func without(ids []string, id string) []string {
	out := ids[:0]
	for _, v := range ids {
		if v != id {
			out = append(out, v)
		}
	}
	return out
}

// ---- cache parity -----------------------------------------------------------

// CacheReq is one chat request through the real SemanticCache exactly as
// server.go's chat handler issues it: Lookup, and on a miss Store with the
// completion. Budget, when set, is applied with SetTenantBudgetBytes first.
type CacheReq struct {
	Tenant     string    `json:"tenant"`
	Text       string    `json:"text"`
	Vector     []float32 `json:"vector"`
	Completion int       `json:"completion_bytes"`
	Budget     *int64    `json:"budget,omitempty"`
	Hit        bool      `json:"hit"`
	Score      float64   `json:"score"`
	UsedBytes  int64     `json:"used_bytes"`
}

type CacheScript struct {
	Seed      int64      `json:"seed"`
	Threshold float64    `json:"threshold"`
	Capacity  int        `json:"capacity"`
	Requests  []CacheReq `json:"requests"`
}

// tableEmbedder returns a fixed vector per key text (cacheKeyText output).
type tableEmbedder struct {
	dims  int
	table map[string][]float32
}

func (e tableEmbedder) Dimensions() int { return e.dims }
func (e tableEmbedder) Model() string   { return "table" }
func (e tableEmbedder) Embed(_ context.Context, texts []string) ([][]float32, error) {
	out := make([][]float32, len(texts))
	for i, text := range texts {
		v, ok := e.table[text]
		if !ok {
			return nil, errors.New("no vector for " + text)
		}
		out[i] = v
	}
	return out, nil
}

func gaussVector(rng *rand.Rand, dims int) []float32 {
	v := make([]float32, dims)
	for i := range v {
		v[i] = float32(rng.NormFloat64())
	}
	return v
}

func cacheScript(seed int64) (CacheScript, error) {
	const dims, bases, variants, requests = 8, 25, 4, 500
	rng := rand.New(rand.NewSource(seed))
	emb := tableEmbedder{dims: dims, table: map[string][]float32{}}
	var texts []string
	for b := 0; b < bases; b++ {
		center := gaussVector(rng, dims)
		for v := 0; v < variants; v++ {
			noise := gaussVector(rng, dims)
			vec := make([]float32, dims)
			for i := range vec {
				vec[i] = center[i] + 0.25*noise[i]
			}
			text := fmt.Sprintf("q%02d-%d", b, v)
			emb.table["user: "+text+"\n"] = embed.Normalize(vec)
			texts = append(texts, text)
		}
	}
	script := CacheScript{Seed: seed, Threshold: 0.9, Capacity: 6}
	cache := gateway.NewSemanticCache(emb, script.Threshold).WithCapacity(script.Capacity)
	ctx := context.Background()
	budgets := map[int]int64{200: 1500, 300: 0, 380: -1}
	for i := 0; i < requests; i++ {
		req := CacheReq{Tenant: []string{"a", "b"}[rng.Intn(2)], Text: texts[rng.Intn(len(texts))],
			Completion: 1 + rng.Intn(1000)}
		if b, ok := budgets[i]; ok {
			req.Tenant, req.Budget = "b", &b
			cache.SetTenantBudgetBytes("b", b)
		}
		req.Vector = emb.table["user: "+req.Text+"\n"]
		msgs := []gateway.Message{{Role: "user", Content: req.Text}}
		if _, score, ok := cache.Lookup(ctx, req.Tenant, msgs); ok {
			req.Hit, req.Score = true, score
		} else {
			cache.Store(ctx, req.Tenant, msgs, strings.Repeat("y", req.Completion))
		}
		req.UsedBytes = cache.UsedBytes(req.Tenant)
		script.Requests = append(script.Requests, req)
	}
	return script, nil
}

func runParity() error {
	var policies []PolicyScript
	for i, gap := range []float64{1, 60, 1800} {
		for _, name := range []string{"costaware", "lru"} {
			policies = append(policies, policyScript(name, int64(100+i), gap, 1500))
		}
	}
	caches := make([]CacheScript, 0, 2)
	for _, seed := range []int64{7, 8} {
		script, err := cacheScript(seed)
		if err != nil {
			return err
		}
		caches = append(caches, script)
	}
	out := map[string]any{"go_version": runtime.Version(), "policies": policies, "caches": caches}
	enc := json.NewEncoder(os.Stdout)
	return enc.Encode(out)
}

// ---- heap bytes per entry ---------------------------------------------------

// randomEmbedder hands out a fresh unit vector per call, as a real embedder
// allocates one per request.
type randomEmbedder struct {
	dims int
	rng  *rand.Rand
}

func (e randomEmbedder) Dimensions() int { return e.dims }
func (e randomEmbedder) Model() string   { return "random" }
func (e randomEmbedder) Embed(_ context.Context, texts []string) ([][]float32, error) {
	out := make([][]float32, len(texts))
	for i := range texts {
		out[i] = embed.Normalize(gaussVector(e.rng, e.dims))
	}
	return out, nil
}

func heapAlloc() uint64 {
	runtime.GC()
	runtime.GC()
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	return m.HeapAlloc
}

// measure stores n entries through the real Store path (one tenant, no
// eviction) and returns the retained heap bytes per entry.
func measure(lengths [][2]int, n, dims int) (perEntry, meanKey, meanCompletion float64) {
	emb := randomEmbedder{dims: dims, rng: rand.New(rand.NewSource(int64(n)))}
	before := heapAlloc()
	cache := gateway.NewSemanticCache(emb, 0.95).WithCapacity(n + 1)
	ctx := context.Background()
	var sumKey, sumCompletion float64
	for i := 0; i < n; i++ {
		l := lengths[i%len(lengths)]
		// A unique 8-digit prefix keeps every key text (so every doc ID)
		// distinct; Upsert would otherwise collapse equal keys into one doc.
		content := fmt.Sprintf("%08d", i) + strings.Repeat("x", max(0, l[0]-len("user: \n")-8))
		msgs := []gateway.Message{{Role: "user", Content: content}}
		cache.Store(ctx, "t", msgs, strings.Repeat("y", l[1]))
		sumKey += float64(len(content) + len("user: \n"))
		sumCompletion += float64(l[1])
	}
	after := heapAlloc()
	runtime.KeepAlive(cache)
	return (float64(after) - float64(before)) / float64(n), sumKey / float64(n), sumCompletion / float64(n)
}

func readLengths(path string) ([][2]int, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		return nil, err
	}
	var out [][2]int
	for i, row := range rows {
		if i == 0 || len(row) < 2 {
			continue // header
		}
		k, err1 := strconv.Atoi(row[0])
		c, err2 := strconv.Atoi(row[1])
		if err1 != nil || err2 != nil {
			return nil, fmt.Errorf("line %d: not two integers", i+1)
		}
		out = append(out, [2]int{k, c})
	}
	if len(out) == 0 {
		return nil, errors.New("no lengths in " + path)
	}
	return out, nil
}

func runMem(args []string) error {
	fs := flag.NewFlagSet("mem", flag.ContinueOnError)
	lengthsPath := fs.String("lengths", "", "CSV with header: key_bytes,completion_bytes")
	dims := fs.Int("dims", 384, "embedding dimensions")
	if err := fs.Parse(args); err != nil {
		return err
	}
	lengths, err := readLengths(*lengthsPath)
	if err != nil {
		return err
	}
	type run struct {
		Lengths        string  `json:"lengths"`
		N              int     `json:"n"`
		BytesPerEntry  float64 `json:"heap_bytes_per_entry"`
		MeanKey        float64 `json:"mean_key_bytes"`
		MeanCompletion float64 `json:"mean_completion_bytes"`
	}
	var runs []run
	minimal := [][2]int{{len("user: \n"), 0}}
	for _, n := range []int{500, 1000, 2000, 4000} {
		for _, set := range []struct {
			name string
			l    [][2]int
		}{{"minimal", minimal}, {"sample", lengths}} {
			b, k, c := measure(set.l, n, *dims)
			runs = append(runs, run{set.name, n, b, k, c})
		}
	}
	out := map[string]any{"go_version": runtime.Version(), "goarch": runtime.GOARCH, "dims": *dims,
		"lengths_file_rows": len(lengths), "runs": runs}
	return json.NewEncoder(os.Stdout).Encode(out)
}
