// k6 load test: 50 concurrent virtual users hammering the rate-limited endpoint.
//
// Run:
//   k6 run scripts/loadtest.js
//   k6 run -e BASE_URL=http://localhost:8000 scripts/loadtest.js
//
// This measures the overhead the rate limiter adds on top of raw request
// handling -- it deliberately uses MANY distinct client IDs (via X-API-Key)
// so most requests are allowed through, letting you isolate "how much
// latency does the Redis round-trip add" from "how often do we reject".
// See loadtest_single_client.js for a companion script that hammers ONE
// client past its limit, to test 429 behavior under sustained overage.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

const errorRate = new Rate('errors');
const rateLimitedRate = new Rate('rate_limited');
const latencyTrend = new Trend('request_duration', true);

export const options = {
  scenarios: {
    sustained_load: {
      executor: 'constant-vus',
      vus: 50,               // 50 concurrent clients, matching the target
      duration: '30s',
    },
  },
  thresholds: {
    // fails the test run if these aren't met -- tune to your actual target
    'http_req_duration{expected_response:true}': ['p(99)<15'],
    errors: ['rate<0.01'],
  },
};

export default function () {
  // Spread requests across many client IDs so most stay under their limit.
  const clientId = `loadtest-client-${__VU}`;

  const res = http.get(`${BASE_URL}/api/resource`, {
    headers: { 'X-API-Key': clientId },
  });

  latencyTrend.add(res.timings.duration);
  rateLimitedRate.add(res.status === 429);
  errorRate.add(res.status >= 500);

  check(res, {
    'status is 200 or 429': (r) => r.status === 200 || r.status === 429,
    'has rate limit headers': (r) => r.headers['X-Ratelimit-Limit'] !== undefined,
  });

  sleep(0.05);
}
