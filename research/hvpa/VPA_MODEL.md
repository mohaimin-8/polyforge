# WP17: what the Vertical Pod Autoscaler does, for the simulation twin

Programme 2, WP17 (horizontal with vertical autoscaling). The twin's A1 and A2 arms run the VPA as
the upstream code does, so its rules are taken from the source, not from memory. Read 2026-11-14 from
the `master` branch of kubernetes/autoscaler; the arms record the VPA release they run.

## Recommender (defaults)

Source: `vertical-pod-autoscaler/docs/flags.md`, `pkg/recommender/logic/recommender.go`,
`pkg/recommender/logic/estimator.go`.

| quantity | rule | default |
|---|---|---|
| target | CPU-usage histogram percentile x (1 + margin) | p90, margin 0.15 |
| lower bound | p50 x (1 + margin) x (1 + 0.001 / c)^-2 | |
| upper bound | p95 x (1 + margin) x (1 + 1 / c)^1 | |
| confidence c | min(lifespan / interval, samples / interval-in-minutes) | interval 24 h, one sample a minute |
| floor | every estimate at least the minimum | 25 millicores |
| histogram decay | exponential | half-life 24 h |

With h hours of history at one sample a minute, c = h / 24. After 1 h the upper bound is
p95 x 1.15 x 25 and the lower bound p50 x 1.15 x 0.954; after 2 h, x 13 and x 0.977.

## Updater

Source: `pkg/updater/priority/update_priority_calculator.go`, `docs/flags.md`, `docs/features.md`.

A pod is updated when its request lies outside [lower bound, upper bound], or after a quick OOM, or
when it has run longer than the pod-lifetime threshold (12 h) and the relative difference between
requested and recommended resources is at least the update threshold (0.1). The updater runs every
minute (`updater-interval` 1m) and by default skips controllers with fewer than 2 replicas
(`min-replicas` 2), unless the VPA object's update policy sets its own `minReplicas`.

So within a run of a few hours with a fresh VPA, the upper bound is far above any request and the
12-hour path is closed: the VPA raises a pod's CPU when the request falls below about the median usage
x 1.1 and does not lower it. The twin models this ratchet; a VPA configured with a shorter
confidence interval would behave differently, and the arms record the flags they run with.

## In-place resize

Source: `docs/features.md`. The `InPlaceOrRecreate` update mode was alpha in VPA 1.4.0, beta in
1.5.0 and GA in 1.6.0 (its feature gate on by default from 1.5.0, removed in 1.7.0); it needs
Kubernetes 1.33 or later with `InPlacePodVerticalScaling`. The updater tries an in-place resize first
and recreates the pod when the resize is infeasible (for example, no room on the node), when the QoS
class would change, when a resize stays deferred more than 5 minutes, or when one is in progress for
more than 1 hour. All containers of a pod are resized together.
