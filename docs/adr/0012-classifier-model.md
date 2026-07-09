# ADR 0012: Workload classifier — softmax LR in Go, ONNX deferred

Status: accepted · 2026-07-10 · trainer + artifacts implemented (W26)

## Context

W26 calls for comparing logistic regression, XGBoost, and a small LSTM,
exporting the winner to ONNX, and running it in Go via onnxruntime. Two
local constraints changed the execution order: no Python ML stack on this
machine (pandas only) and, more fundamentally, no real trace data yet —
the LMSYS/Azure/Alibaba downloads (W25b) haven't run, so any model-family
comparison now would rank models on synthetic data whose winner says
nothing about the real ranking.

## Decision

1. **Ship the full pipeline now with multinomial logistic regression
   implemented in Go** (`internal/classifier`): 12-feature engineering,
   deterministic full-batch trainer, single-path evaluation
   (`Evaluate`), JSON model artifact with feature/class-name guards
   against index drift. The W19 rule classifier stays as the baseline
   candidate and the fallback when no artifact is present.
2. **Defer the XGBoost/LSTM comparison to the real-trace phase**, where it
   can be honest. The comparison harness (one `predict` closure per
   candidate through one evaluator) is the part that had to exist first,
   and it now does.
3. **JSON weights instead of ONNX.** ONNX Runtime's Go binding requires
   cgo plus a platform-specific native library — a heavy dependency to
   carry through the distroless image and the CI matrix for a model that
   is a 5×12 matrix multiply. The 50ms online budget is met by four
   orders of magnitude (~0.7 µs/inference, measured). If the real-trace
   comparison selects a tree ensemble, ONNX becomes the right transport
   and this decision is revisited — the artifact-loading seam
   (`classifier.LoadModel`) is where it would slot in.

## Consequences

- The W27 online classifier consumes `artifacts/classifier-model.json`
  and needs no new dependencies.
- The synthetic evaluation (rules 0.885 vs LR 1.000 macro-F1, seeded,
  reproducible via `cmd/classifier-train`) validates the pipeline and
  demonstrates the rules' brittleness under mixed windows; it is not a
  research result and is labeled as such everywhere it appears
  (research/TAXONOMY.md).
- Feature order is a compatibility contract: `FeatureNames` is embedded
  in every artifact and `LoadModel` rejects drift.
