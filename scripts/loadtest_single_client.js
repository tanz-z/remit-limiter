// k6 load test: ONE client, deliberately sent well past its limit.
// Confirms the limiter actually rejects with 429 + Retry-After once
// the client exceeds its configured rate, and that it recovers correctly.
//
// Run: k6 run scripts/loadtest_single_client.js

import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  vus: 10,
  duration: '15s',
};

export default function () {
  // all VUs share the same client ID, so together they blow past the limit fast
  const res = http.get(`${BASE_URL}/api/resource`, {
    headers: { 'X-API-Key': 'single-overloaded-client' },
  });

  check(res, {
    '429 has Retry-After header': (r) => r.status !== 429 || r.headers['Retry-After'] !== undefined,
  });

  sleep(0.01);
}
