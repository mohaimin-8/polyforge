# Over-the-wire cache side-channel — as measured (PREREG_WIRE_ATTACK.md)

Substrate (prereg amendment): the real `cmd/ai-gateway` process with the
deployed local n-gram embedder + a mock LLM backend, attacked over loopback;
exact-prompt membership threat. Real HTTP + real cache + real timing, no WAN
jitter. AUC by the same Mann-Whitney statistic the simulator uses.

- **WA-H1 (defense, primary): PASS.** Per-tenant posture membership AUC 0.502 (95% CI [0.384, 0.612]) — indistinguishable from chance 0.50.
- **WA-H2 (attack reality, secondary): channel present.** Shared posture AUC 1.000 (95% CI [1.000, 1.000]); no magnitude was pre-committed — real RTT jitter is expected to weaken the sim's 0.88 upper bound, and whatever it is, is reported as measured.
- **WA-H3 (timing gap, descriptive):** measured hit median 15.6 ms vs miss median 98.7 ms on the wire (sim assumes 20 ms / 800 ms).
