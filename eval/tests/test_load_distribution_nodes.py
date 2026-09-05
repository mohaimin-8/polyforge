"""Node attribution for the load-distribution sampler.

`FINAL_ROADMAP` records W7 ("single machine, single cluster") as needing a
high-demand cell, i.e. the GPU-blocked B1 matrix. That reads the constraint off
the wrong axis. `cluster_backend.NODES_BY_SIZE` already gives `small` two nodes
and `medium` four, and the CRUD path needs no GPU whatsoever -- the 24 h soak
drove 298 rps across sixteen pods.

What was actually missing is the measurement. `LoadDistributionSampler` reads
`kubectl top pods`, which carries a pod name and a CPU figure and nothing else,
so every live record reports per-POD spread and no record anywhere says which
NODE served anything. No sitting, at any demand, could have produced node-level
evidence, because node placement was never collected.

These tests pin the aggregation rather than the cluster: the sampler's shape is
what a future sitting depends on.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval"))

from harness.cluster_backend import LoadDistributionSampler  # noqa: E402


def sampler_with(cpu_ms: dict[str, float], node_of: dict[str, str]) -> LoadDistributionSampler:
    sampler = LoadDistributionSampler()
    sampler.cpu_ms = dict(cpu_ms)
    sampler.node_of = dict(node_of)
    return sampler


def test_cpu_is_aggregated_by_node_not_just_by_pod():
    # Arrange: four pods, two nodes, deliberately uneven within each node.
    sampler = sampler_with(
        {"pod-a": 300.0, "pod-b": 100.0, "pod-c": 400.0, "pod-d": 200.0},
        {"pod-a": "node-1", "pod-b": "node-1", "pod-c": "node-2", "pod-d": "node-2"})

    # Act
    nodes = sampler.node_shares()

    # Assert: 400/1000 and 600/1000, and the pod view is unchanged.
    assert nodes == {"node-1": 0.4, "node-2": 0.6}
    assert len(sampler.shares()) == 4


def test_a_single_node_cluster_is_visible_as_one_node_carrying_everything():
    # This is the reading L5's sitting would have produced had the measurement
    # existed: multi-node cluster, one pod, all load on one node.
    sampler = sampler_with({"pod-a": 500.0}, {"pod-a": "kind-worker"})
    assert sampler.node_shares() == {"kind-worker": 1.0}


def test_an_unresolved_pod_is_grouped_not_dropped():
    # Arrange: a pod whose node lookup failed.
    sampler = sampler_with({"pod-a": 250.0, "pod-b": 250.0}, {"pod-a": "node-1"})

    # Act
    nodes = sampler.node_shares()

    # Assert: the shares still account for all observed CPU, which is what
    # makes the number safe to quote. Silently dropping the pod would report
    # node-1 at 100% of a cluster where it did half the work.
    assert nodes == {"node-1": 0.5, "unknown": 0.5}
    assert sum(nodes.values()) == 1.0


def test_no_samples_yields_no_shares_rather_than_a_divide_by_zero():
    assert sampler_with({}, {}).node_shares() == {}


def test_node_lookup_failure_does_not_count_as_a_sampling_failure():
    # `failures` gates the run's validity: a CPU reading that arrived is still
    # a good sample even when the placement lookup for it did not.
    sampler = LoadDistributionSampler()
    before = sampler.failures
    sampler._resolve_node("a-pod-that-does-not-exist")
    assert sampler.failures == before
    assert "a-pod-that-does-not-exist" not in sampler.node_of
