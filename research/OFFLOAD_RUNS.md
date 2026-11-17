# Programme 2's CPU campaigns on GitHub Actions: which code ran

On 2026-11-17 the CPU campaigns of `research/run_queue.py` ran on GitHub Actions
(`research/offload.py`, `.github/workflows/campaign.yml`) while the laptop ran
the WP17 live pilot. Each was started by pushing a branch `offload/<campaign>`
at a snapshot commit: research-v2's tree as one commit on `main`, made while the
branch was not yet public. The `offload/*` branches were deleted once every
result was committed here, so a snapshot may no longer be reachable on GitHub;
each one's tree is identical, file for file, to the research-v2 commit named
below, which is. Every result commit names its run, platform and Python
version in its message.

| campaign | GitHub run | ran on snapshot | same tree as research-v2 | committed here |
|---|---|---|---|---|
| wp9-A-own-plant | [44077794828](https://github.com/mohaimin-8/polyforge/actions/runs/44077794828) | `63db981` | `4d23408` | `2a9f38a` |
| morris-r20 | [44077801476](https://github.com/mohaimin-8/polyforge/actions/runs/44077801476) | `63db981` | `4d23408` | `52d9241` |
| wp9-B-native | [44077807977](https://github.com/mohaimin-8/polyforge/actions/runs/44077807977) | `63db981` | `4d23408` | `2609705` |
| wp9-A-native | [44077814413](https://github.com/mohaimin-8/polyforge/actions/runs/44077814413) | `63db981` | `4d23408` | `29027e4` |
| sobol | [44083963230](https://github.com/mohaimin-8/polyforge/actions/runs/44083963230) | `8c92e17` | `52d9241` | `b78f8da` |
| speccurve-runs, speccurve | [44086805185](https://github.com/mohaimin-8/polyforge/actions/runs/44086805185) | `706c988` | `2a9f38a` | `3bfe24f`, `e1d98b8` |

Checks run the same way (nothing committed by them):

| check | GitHub run | ran on | result |
|---|---|---|---|
| reproduce | [44093832991](https://github.com/mohaimin-8/polyforge/actions/runs/44093832991) | snapshot `3c1090c` = research-v2 `529b5a0` | 8 steps changed: 7 in the last digits of numbers, 2 real faults (fixed in the commits that follow) |
| reproduce | [44096198225](https://github.com/mohaimin-8/polyforge/actions/runs/44096198225) | research-v2 `e8aaeba` | the two faults fixed |
| reproduce | [44096617521](https://github.com/mohaimin-8/polyforge/actions/runs/44096617521) | research-v2 `87bd080` | programme 2 reproduces; 7 steps differ in the last digits only, within 1e-6 |
| secret scan | [44098763731](https://github.com/mohaimin-8/polyforge/actions/runs/44098763731) | research-v2 `eb98d42` | one gitleaks finding, a public image digest (allowlisted in `.gitleaks.toml`) |

Four first attempts (runs 44077628934, 44077639295, 44077649598, 44077659054, on
snapshot `5454c6c` = research-v2 `1a92be6`) stopped at setup: the runner image
had no Python 3.13.5. Nothing ran; the campaigns above are their restarts.

Before any campaign ran, a cross-check on the runner re-ran committed WP9
evaluation runs of the laptop (five per campaign, every arm, both own-mix
campaigns then committed) and got identical numbers; `full_curve.py`'s own
re-check did the same for all four own-mix campaigns. A run on the runner
computes what it computes on the laptop.
