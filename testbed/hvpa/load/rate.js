// WP17 load (programme 2): open-loop requests to the CPU service at a schedule
// of rates, one rate per step (research/hvpa/workloads.py), run in the cluster
// (kind maps no host port for the service). k6's ramping-arrival-rate moves
// linearly within a stage, so each step is a 0 s stage (a jump) and a held
// stage: the twin's piecewise-constant rate.
//
// A new connection per request (noConnectionReuse): kube-proxy balances per
// connection, and kept-alive connections would never reach pods added by a
// scale-out. A request is on time when it answers 200 within the schedule's
// SLO; a request k6 could not start (dropped_iterations) counts as late.
// The summary goes to stdout between markers for the host to collect.
//
//   k6 run -e SCHEDULE=/m/schedule.json /m/rate.js
import http from 'k6/http';
import { Rate, Trend } from 'k6/metrics';

const S = JSON.parse(open(__ENV.SCHEDULE));
const TARGET = __ENV.TARGET || 'http://cpuburn.pf-hvpa/work';

const latency = new Trend('wp17_latency', true);
const onTime = new Rate('wp17_on_time');

const stages = [];
for (const r of S.rates) {
  stages.push({ target: r, duration: '0s' });
  stages.push({ target: r, duration: `${S.step_s}s` });
}

export const options = {
  noConnectionReuse: true,
  discardResponseBodies: true,
  scenarios: {
    wp17: {
      executor: 'ramping-arrival-rate',
      startRate: S.rates[0],
      timeUnit: '1s',
      preAllocatedVUs: S.preallocated_vus || 50,
      maxVUs: S.max_vus || 400,
      stages,
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max', 'count'],
};

export default function () {
  const res = http.get(TARGET, { timeout: `${S.timeout_s || 60}s` });
  latency.add(res.timings.duration);
  onTime.add(res.status === 200 && res.timings.duration <= S.slo_s * 1000);
}

export function handleSummary(data) {
  return { stdout: `-----BEGIN WP17 LOAD-----\n${JSON.stringify(data)}\n-----END WP17 LOAD-----\n` };
}
