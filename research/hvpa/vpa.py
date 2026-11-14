"""The Vertical Pod Autoscaler's CPU recommender and updater, for the WP17 twin
(programme 2). Mirrors kubernetes/autoscaler `vertical-pod-autoscaler` on master,
read 2026-11-14; VPA_MODEL.md lists the sources and what follows from them.

Recommender, per container across all pods of the target:
  histogram  exponential buckets (first 0.01 core, ratio 1.05, max 1000 cores,
             util/histogram_options.go); Percentile returns the END of the
             bucket that crosses the threshold (start of the last bucket);
  decay      a sample's weight x 2^((t - reference) / half-life), half-life
             24 h; the reference moves to a multiple of the half-life when the
             factor would pass 2^100 (decaying_histogram.go);
  samples    weight minSampleWeight = 0.1 each (aggregate_container_state.go);
  estimates  target p90, lower p50, upper p95, each x (1 + 0.15); lower x
             (1 + 0.001 / c)^-2, upper x (1 + 1 / c); every one at least 25
             millicores (recommender.go, estimator.go); c = min(lifespan,
             samples per minute) in units of the 24 h confidence interval;
  policy     the VPA object's minAllowed / maxAllowed cap all three.
Updater, every minute (update_priority_calculator.go, docs/features.md): a
pod is updated when its request is outside [lower, upper], or when it has
run past 12 h and the relative difference of request and target is at least
0.1; none when the target has fewer than min-replicas (2) pods. The chart
WP17 installs skips the disruption budget for in-place updates, so every
eligible pod is resized in one loop (without that flag, at most
max(1, floor(replicas x 0.5)) per loop, the eviction tolerance).
In place (InPlaceOrRecreate, GA in VPA 1.6): the pod's request becomes the
target without a restart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

FIRST_BUCKET = 0.01
RATIO = 1.05
MAX_VALUE = 1000.0
MIN_SAMPLE_WEIGHT = 0.1
EPSILON = 0.001 * MIN_SAMPLE_WEIGHT
HALF_LIFE_S = 24 * 3600.0
MAX_DECAY_EXPONENT = 100
CONFIDENCE_INTERVAL_S = 24 * 3600.0
MARGIN = 0.15
MIN_CPU = 0.025
TARGET_P, LOWER_P, UPPER_P = 0.9, 0.5, 0.95
UPDATER_INTERVAL_S = 60.0
POD_LIFETIME_THRESHOLD_S = 12 * 3600.0
UPDATE_THRESHOLD = 0.1
EVICTION_TOLERANCE = 0.5
MIN_REPLICAS = 2
SETTINGS = {"default": {"half_life_s": HALF_LIFE_S, "confidence_interval_s": CONFIDENCE_INTERVAL_S},
            "fast": {"half_life_s": 600.0, "confidence_interval_s": 3600.0}}
NUM_BUCKETS = int(math.ceil(math.log(MAX_VALUE * (RATIO - 1) / FIRST_BUCKET + 1, RATIO))) + 1


def find_bucket(value: float) -> int:
    if value < FIRST_BUCKET:
        return 0
    bucket = int(math.log(value * (RATIO - 1) / FIRST_BUCKET + 1, RATIO))
    return min(bucket, NUM_BUCKETS - 1)


def bucket_start(bucket: int) -> float:
    return FIRST_BUCKET * (RATIO ** bucket - 1) / (RATIO - 1)


@dataclass
class DecayingHistogram:
    half_life_s: float = HALF_LIFE_S
    weights: dict = field(default_factory=dict)
    total: float = 0.0
    min_bucket: int = NUM_BUCKETS - 1
    max_bucket: int = 0
    reference_s: float = 0.0

    def _shift(self, t: float) -> None:
        new_ref = round(t / self.half_life_s) * self.half_life_s
        exponent = math.floor((self.reference_s - new_ref) / self.half_life_s + 0.5)
        scale = 2.0 ** exponent
        self.weights = {b: w * scale for b, w in self.weights.items()}
        self.total *= scale
        self.reference_s = new_ref

    def add(self, value: float, weight: float, t: float) -> None:
        if t > self.reference_s + self.half_life_s * MAX_DECAY_EXPONENT:
            self._shift(t)
        w = weight * 2.0 ** ((t - self.reference_s) / self.half_life_s)
        b = find_bucket(value)
        self.weights[b] = self.weights.get(b, 0.0) + w
        self.total += w
        if b < self.min_bucket and self.weights[b] >= EPSILON:
            self.min_bucket = b
        if b > self.max_bucket and self.weights[b] >= EPSILON:
            self.max_bucket = b

    def empty(self) -> bool:
        return self.total < EPSILON

    def percentile(self, p: float) -> float:
        if self.empty():
            return 0.0
        threshold, partial, bucket = p * self.total, 0.0, self.min_bucket
        while bucket < self.max_bucket:
            partial += self.weights.get(bucket, 0.0)
            if partial >= threshold:
                break
            bucket += 1
        return bucket_start(bucket + 1) if bucket < NUM_BUCKETS - 1 else bucket_start(bucket)


@dataclass
class Recommendation:
    target: float
    lower: float
    upper: float


@dataclass
class Recommender:
    """One container's aggregate state across the target's pods. The two
    durations are recommender flags (--cpu-histogram-decay-half-life,
    --confidence-interval-cpu); FAST is a declared non-default setting that
    lets the VPA act within an hour-long run."""
    min_allowed: float = 0.0
    max_allowed: float = math.inf
    half_life_s: float = HALF_LIFE_S
    confidence_interval_s: float = CONFIDENCE_INTERVAL_S
    hist: DecayingHistogram | None = None
    samples: int = 0
    first_s: float | None = None
    last_s: float | None = None

    def __post_init__(self) -> None:
        if self.hist is None:
            self.hist = DecayingHistogram(half_life_s=self.half_life_s)

    def add_sample(self, usage_cores: float, t: float) -> None:
        self.hist.add(usage_cores, MIN_SAMPLE_WEIGHT, t)
        self.first_s = t if self.first_s is None else min(self.first_s, t)
        self.last_s = t if self.last_s is None else max(self.last_s, t)
        self.samples += 1

    def confidence(self) -> float:
        if self.first_s is None:
            return 0.0
        lifespan = (self.last_s - self.first_s) / self.confidence_interval_s
        return min(lifespan, self.samples / (self.confidence_interval_s / 60.0))

    def recommend(self) -> Recommendation | None:
        if self.hist.empty():
            return None
        c = self.confidence()
        lower_mult = (1.0 + 0.001 / c) ** -2.0 if c > 0 else 0.0
        upper_mult = (1.0 + 1.0 / c) if c > 0 else math.inf

        def est(p: float, mult: float) -> float:
            v = max(self.hist.percentile(p) * (1.0 + MARGIN) * mult, MIN_CPU)
            return min(max(v, self.min_allowed), self.max_allowed)

        return Recommendation(target=est(TARGET_P, 1.0), lower=est(LOWER_P, lower_mult),
                              upper=est(UPPER_P, upper_mult))


def pods_to_update(requests: list[float], ages_s: list[float], rec: Recommendation | None,
                   min_replicas: int = MIN_REPLICAS, skip_disruption_budget: bool = True) -> list[int]:
    """Indices of the pods one updater loop resizes to the target. The VPA
    chart (0.15.0) runs the updater with --in-place-skip-disruption-budget=true,
    so in-place updates of NotRequired containers skip the eviction tolerance
    and every eligible pod is resized in the same loop; False applies it."""
    if rec is None or len(requests) < min_replicas:
        return []
    chosen = []
    for i, (req, age) in enumerate(zip(requests, ages_s)):
        outside = req < rec.lower or req > rec.upper
        diff = abs(rec.target - req) / req if req > 0 else math.inf
        if outside or (age >= POD_LIFETIME_THRESHOLD_S and diff >= UPDATE_THRESHOLD):
            if rec.target != req:
                chosen.append((not (rec.target > req), -diff, i))  # scale-ups first, then larger diffs
    chosen.sort()
    allowed = len(chosen) if skip_disruption_budget else max(1, int(len(requests) * EVICTION_TOLERANCE))
    return [i for _, _, i in chosen[:allowed]]
