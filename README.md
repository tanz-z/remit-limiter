# Distributed Rate Limiter

A rate limiter for FastAPI that enforces limits consistently across multiple
service instances, using Redis as shared state. Supports token bucket and
sliding window algorithms, per-client/per-IP limits, and configurable
fail-open/fail-closed behavior if Redis becomes unavailable.

## Why this exists

If you run more than one instance of an API behind a load balancer, an
in-memory rate limiter is useless -- each instance only sees its own slice of
traffic, so a client can get `N` requests through *each* instance instead of
`N` total. The fix is to make every instance check the same shared counter.
Redis is a natural fit: it's fast, all your instances can already reach it,
and its Lua scripting lets you do "read the counter, decide, update it" as
one atomic step so concurrent requests from different instances can't race
each other into over-allowing traffic.

## Project layout

```
app/
  config.py            # env-driven settings, per-client limit overrides
  redis_client.py       # shared async Redis connection pool
  middleware.py          # the FastAPI middleware -- the reusable part
  main.py                 # example app wiring the middleware in
  limiters/
    base.py                # shared LimitResult type
    token_bucket.py          # token bucket, Lua-script atomic
    sliding_window.py        # sliding window log, Lua-script atomic
tests/
  test_limiters.py            # unit tests (fakeredis, no real Redis needed)
scripts/
  loadtest.js                   # k6: 50 concurrent clients, checks p99 latency
  loadtest_single_client.js       # k6: one client blown past its limit, checks 429s
docker-compose.yml
Dockerfile
```

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements-dev.txt --break-system-packages   # includes test deps

# 2. Run Redis locally (or use docker-compose, see below)
redis-server --daemonize yes

# 3. Run the app
uvicorn app.main:app --reload

# 4. Try it
curl -H "X-API-Key: my-client" http://localhost:8000/api/resource
```

Or with Docker Compose (spins up Redis + the app together):

```bash
docker compose up --build
```

## Running the tests

```bash
pip install -r requirements-dev.txt --break-system-packages
pytest -v
```

Tests run against `fakeredis`, so no real Redis instance is needed. The
`test_concurrent_requests_do_not_exceed_limit` test is the important one --
it fires 50 concurrent requests at a bucket sized for 10 and asserts exactly
10 get through, which is the whole point of doing the check atomically in
Lua instead of as separate Python-side GET/SET calls.

## Load testing with k6

Install [k6](https://k6.io/docs/get-started/installation/), then:

```bash
# Baseline: 50 concurrent clients hammering the API, checks p99 < 15ms
k6 run scripts/loadtest.js

# Point at a non-default host
k6 run -e BASE_URL=http://your-host:8000 scripts/loadtest.js

# Confirm 429 + Retry-After behavior when one client exceeds its limit
k6 run scripts/loadtest_single_client.js
```

`loadtest.js` spreads requests across many client IDs (one per virtual
user) so most requests are allowed -- this isolates "how much latency does
the rate-limit check itself add" from "how often do we reject." It has a
threshold requiring p99 latency under 15ms; tune this to your actual Redis
setup (a local Redis will be much faster than one over a network hop to a
managed instance).

## Configuration

All via environment variables, see `app/config.py`:

| Variable | Default | Meaning |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `RATE_LIMIT_ALGORITHM` | `token_bucket` | `token_bucket` or `sliding_window` |
| `RATE_LIMIT_FAILURE_MODE` | `fail_open` | `fail_open` or `fail_closed` when Redis is unreachable |
| `RATE_LIMIT_DEFAULT_CAPACITY` | `100` | Token bucket size (burst allowance) |
| `RATE_LIMIT_DEFAULT_REFILL_RATE` | `50` | Token bucket refill rate, tokens/sec |
| `RATE_LIMIT_DEFAULT_WINDOW_SECONDS` | `1` | Sliding window duration |
| `RATE_LIMIT_DEFAULT_WINDOW_LIMIT` | `100` | Sliding window request cap |
| `RATE_LIMIT_IDENTITY_SOURCE` | `api_key` | `api_key` (reads `X-API-Key` header) or `ip` |

Per-client overrides live in `PER_CLIENT_LIMITS` in `app/config.py` --
swap that dict for a database or config-service lookup in a real deployment.

## Design notes worth knowing for an interview / write-up

- **Why Lua scripts, not Python-side check-then-set:** two app instances
  could both read "3 tokens left," both decide to allow a request, and both
  decrement -- over-allowing traffic. Redis runs Lua scripts atomically
  (single-threaded), so the whole check-and-update happens as one
  indivisible unit no matter how many instances are hitting Redis at once.
  The `test_concurrent_requests_do_not_exceed_limit` test proves this.

- **Token bucket vs. sliding window:** token bucket allows short bursts up
  to the bucket capacity, which suits bursty legitimate traffic (a user
  clicking rapidly). Sliding window enforces a stricter, smoother rate and
  avoids the "fixed window" boundary problem where a client could send
  `limit` requests right at the end of one window and `limit` more at the
  start of the next.

- **Fail-open vs. fail-closed:** if Redis goes down, fail-open lets all
  traffic through (protects availability, risks your backend getting
  overwhelmed with no rate limiting); fail-closed rejects everything
  (protects your backend, turns a Redis outage into a full API outage).
  Which one is right depends on what's more expensive for your system --
  most APIs default to fail-open since an unprotected-but-up API usually
  beats a fully-down one.

- **Short Redis timeouts:** the client is configured with a 50ms socket
  timeout specifically so a struggling Redis doesn't stall every request
  behind it -- better to fail fast into the fail-open/fail-closed path than
  let requests queue up waiting on a slow dependency.

## Extending this

- Swap `PER_CLIENT_LIMITS` for a real lookup (Postgres, Redis hash, config
  service) if you need limits to change without a redeploy.
- Add a `Retry-After`-aware client-side backoff example if consumers of your
  API need guidance on how to handle 429s gracefully.
- For very high throughput, consider Redis Cluster and make sure related
  keys for a client hash to the same slot (they will here, since each
  client's data lives under one key).
