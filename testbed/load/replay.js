// WP10 load: replay a schedule of real prompts at their recorded arrival times
// against the PolyForge gateway (programme 2). The schedule comes from
// make_schedule.py; tokens.json from scripts/provision.py.
//
// Open loop. Every request has a due time, start + LEAD_S + t. A VU claims the
// next request in schedule order, sleeps until it is due and sends it, so
// arrivals follow the trace rather than the system's speed. VUS bounds the
// requests in flight plus those waiting for their time; a request that leaves
// late is measured (wp10_schedule_lag) and gated, never dropped silently.
//
// Paths in SCHEDULE and TOKENS are resolved by k6 relative to this file, so
// `-e SCHEDULE=../out/...` works from any working directory on any platform.
//
//   k6 run -e SCHEDULE=../out/schedules/pop007/schedule.json -e TOKENS=../out/tokens.json \
//     --summary-export testbed/out/runs/r1/k6-summary.json \
//     --out csv=testbed/out/runs/r1/k6-samples.csv.gz testbed/load/replay.js
import http from 'k6/http';
import exec from 'k6/execution';
import { sleep } from 'k6';
import { SharedArray } from 'k6/data';
import { Counter, Trend } from 'k6/metrics';

const SCHEDULE_PATH = __ENV.SCHEDULE;
const TOKENS_PATH = __ENV.TOKENS;
if (!SCHEDULE_PATH || !TOKENS_PATH) {
  throw new Error('set -e SCHEDULE=<schedule.json> and -e TOKENS=<tokens.json>');
}
const GATEWAY = (__ENV.GATEWAY_URL || 'http://127.0.0.1:30081').replace(/\/$/, '');
const LEAD_S = Number(__ENV.LEAD_S || 15);
// Optional shared clock: schedule time 0 at this unix time (ms), the same
// --start the actuator's replay is given, so decisions and load line up.
// It must lie a few seconds after `k6 run` starts (VU set-up), and less than
// LEAD_S + 900 s after it.
const START_UNIX_MS = __ENV.START_UNIX_MS ? Number(__ENV.START_UNIX_MS) : null;
const VUS = Number(__ENV.VUS || 300);
const TIMEOUT = __ENV.REQUEST_TIMEOUT || '300s';
const MAX_FAILED_RATE = Number(__ENV.MAX_FAILED_RATE || 0.01);
const ABORT_ON_FAIL = (__ENV.ABORT_ON_FAIL || 'true') === 'true';
const MAX_P99_LAG_MS = Number(__ENV.MAX_P99_LAG_MS || 1000);

// Parsed once and shared read-only by every VU (a plain JSON.parse here would
// copy the prompts into each of hundreds of VUs).
const REQUESTS = new SharedArray('requests', () => JSON.parse(open(SCHEDULE_PATH)).requests);
const META = new SharedArray('meta', () => [JSON.parse(open(SCHEDULE_PATH)).meta])[0];
const TOKENS = JSON.parse(open(TOKENS_PATH));

const latencyFromDue = new Trend('wp10_latency_from_due', true);
const scheduleLag = new Trend('wp10_schedule_lag', true);
const outcomes = new Counter('wp10_requests');

export const options = {
  scenarios: {
    replay: {
      executor: 'shared-iterations',
      vus: Math.max(1, Math.min(VUS, REQUESTS.length)),
      iterations: REQUESTS.length,
      maxDuration: `${Math.ceil(META.duration_s + LEAD_S + 900)}s`,
    },
  },
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
  thresholds: {
    // More than 1% failed requests is a broken run, not a measurement.
    http_req_failed: [{ threshold: `rate<${MAX_FAILED_RATE}`, abortOnFail: ABORT_ON_FAIL, delayAbortEval: '60s' }],
    // The generator must keep the trace's clock (WP14's lesson: a load
    // generator that falls behind changes the experiment unnoticed).
    wp10_schedule_lag: [`p(99)<${MAX_P99_LAG_MS}`],
  },
};

export function setup() {
  const missing = META.tenants.map((t) => t.id).filter((id) => !TOKENS[id]);
  if (missing.length > 0) {
    throw new Error(`tokens.json has no API key for tenants ${missing.join(', ')}`);
  }
  if (START_UNIX_MS !== null && START_UNIX_MS < Date.now()) {
    throw new Error(`START_UNIX_MS ${START_UNIX_MS} is already in the past`);
  }
  return { requests: REQUESTS.length, duration_s: META.duration_s };
}

function dueTime(req) {
  const zero = START_UNIX_MS !== null ? START_UNIX_MS : exec.scenario.startTime + LEAD_S * 1000;
  return zero + req.t * 1000;
}

function header(res, name) {
  const wanted = name.toLowerCase();
  for (const key of Object.keys(res.headers)) {
    if (key.toLowerCase() === wanted) return res.headers[key];
  }
  return 'none';
}

export default function () {
  const req = REQUESTS[exec.scenario.iterationInTest];
  const due = dueTime(req);
  const wait = due - Date.now();
  if (wait > 0) sleep(wait / 1000);
  scheduleLag.add(Math.max(0, Date.now() - due));

  const res = http.post(
    `${GATEWAY}/v1/tenants/${req.tenant}/ai/chat`,
    JSON.stringify({ messages: [{ role: 'user', content: req.prompt }] }),
    {
      headers: { 'Content-Type': 'application/json', 'X-PolyForge-API-Key': TOKENS[req.tenant] },
      timeout: TIMEOUT,
      tags: { name: 'chat', tenant: req.tenant },
      responseCallback: http.expectedStatuses(200),
    },
  );
  const tags = {
    tenant: req.tenant,
    seq: String(req.seq),
    warmup: String(req.warmup === true),
    status: String(res.status),
    cache: header(res, 'X-PolyForge-Cache'),
    tier: header(res, 'X-PolyForge-Tier'),
  };
  latencyFromDue.add(Date.now() - due, tags);
  outcomes.add(1, { status: tags.status, cache: tags.cache, tier: tags.tier });
}
