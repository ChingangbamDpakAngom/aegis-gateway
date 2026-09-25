# Phase 7 — Ship v1.0: Docker stack, load test, dashboard

**Goal:** anyone can run the whole system with one command, see how it's doing on a dashboard, and trust the performance numbers because they were measured.

## What changed and why

| Before | After | Where |
|---|---|---|
| Run from a dev shell; Redis alone in Docker | `docker compose --profile stack up -d --build` → gateway, Redis, Prometheus, Grafana | `Dockerfile`, `docker-compose.yml` |
| — | Non-root image, deps cached before source; guard optional (`WITH_GUARD=true`, CPU torch) | `Dockerfile` |
| `/metrics` read with curl | Prometheus scrapes every 5 s; Grafana dashboard provisioned from files | `ops/` |
| No performance numbers | Load test: cached / ratelimit / model scenarios | `scripts/load_test.py` |
| Identical concurrent misses each called the model | They share one call (`"cache": "coalesced"`) | `cache.single_flight()`, `main.py` |
| Latency histogram capped at 10 s | Buckets up to 300 s | `observability.py` |
| New status series missed their first burst | `/chat` status series start at 0 | `observability.py` |
| CI ran tests only | CI also builds the Docker image | `.github/workflows/ci.yml` |
| v0.1.0 | **v1.0.0**, new overview diagram, results table and dashboard in the README | `pyproject.toml`, `README.md` |

## How the stack fits together

```
 you ──► :8000 gateway (container, user "aegis") ──► redis:6379 (container)
                     │                         └──► host.docker.internal:11434 Ollama (host GPU)
                     │ /metrics every 5 s
              :9090 Prometheus ──► :3000 Grafana (dashboard from ops/grafana/)
```

## Results

| Scenario | Throughput | p50 | p95 | Notes |
|---|---|---|---|---|
| cached (2000 req, c=20) | 246 req/s | 73 ms | 109 ms | gateway overhead only |
| ratelimit (200 req, c=20) | 61 req/s | 52 ms | 3.1 s | 11 × 200 / 189 × 429; 1 model call (was 11, p95 12.4 s) |
| model (12 req, c=4) | 0.9 req/s | 3.0 s | 6.7 s | limited by the laptop GPU |

## Try it yourself

```bash
docker compose --profile stack up -d --build
docker compose exec gateway python -m gateway.keys loadtest --capacity 1000000 --refill-per-s 1000000
uv run python scripts/load_test.py cached --key <key> --concurrency 20 --requests 2000
# open http://localhost:3000 and watch the panels move
docker compose exec gateway python -m gateway.keys burst --capacity 10 --refill-per-s 1
uv run python scripts/load_test.py ratelimit --key <burst key> --requests 200
docker compose logs gateway | grep coalesced          # identical requests sharing one model call
docker compose --profile stack down
```

## Must-know core (job-ready)

1. **Docker basics:** image vs container, layers and cache order (copy the lock file, install, *then* copy the code), don't run as root, `.dockerignore`.
2. **Compose:** services on one network find each other by name (`redis`, `gateway`); `ports` exposes a service to your machine; volumes persist data; profiles make services optional; healthchecks.
3. **Load testing:** throughput vs latency, p50/p95/p99 (why the tail matters), concurrency, and knowing *what* you're measuring (the cached scenario isolates the gateway, the model scenario isolates the LLM).
4. **Cache stampede and request coalescing:** many identical misses at once flood the backend. Single-flight makes one call and shares the answer.
5. **Prometheus + Grafana:** pull-based scraping, `rate()` / `increase()` / `histogram_quantile()`, why histogram buckets must cover your real latencies, and why counters should start at 0.
6. **Trust your own measurements:** the client-side numbers once disagreed with the gateway's logs. Find which side is wrong before publishing a number.

**Likely interview questions:** "How do you know your service is fast enough?" (load test plus p95 in Grafana). "What's a cache stampede and how do you prevent it?" (coalescing, locks, early refresh). "How would you deploy this?" (the same image to a container platform, Redis managed, the model on a GPU node or a hosted API, secrets from the environment, `/ready` as the readiness probe).

**Skip for now:** Kubernetes manifests, multi-stage builds, distroless images, k6/Locust scripting, Grafana alerting, distributed tracing.

## Study links

- [Docker — Dockerfile best practices](https://docs.docker.com/build/building/best-practices/) (layers, cache, non-root)
- [Docker Compose — profiles](https://docs.docker.com/compose/how-tos/profiles/) and [networking](https://docs.docker.com/compose/how-tos/networking/)
- [Prometheus — histograms and summaries](https://prometheus.io/docs/practices/histograms/) (buckets)
- [Grafana — provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Cache stampede (Wikipedia)](https://en.wikipedia.org/wiki/Cache_stampede): the problem and the three standard fixes
- [Brendan Gregg — latency percentiles](https://www.brendangregg.com/blog/2016-10-27/latency-heat-maps.html) (why averages lie; skim)

## Review checklist — can you explain…

- [ ] Why `uv.lock` is copied before `src/` in the Dockerfile?
- [ ] Why the container runs as `aegis`, and what broke when the model-cache volume was created as root?
- [ ] Why the guard image is 3 GB and the default 372 MB, and why the CPU-only torch wheel?
- [ ] How the gateway container reaches Redis (`redis:6379`) vs Ollama (`host.docker.internal`)?
- [ ] What the three load-test scenarios each measure?
- [ ] What went wrong in the first ratelimit run, and how `single_flight` fixed it (and why `asyncio.shield`)?
- [ ] Why the p95 panel was stuck at 10 s, and why "injection blocked" read 0?
- [ ] Why you trusted the gateway's `duration_ms` over the client when they disagreed?
- [ ] What you'd change before deploying this for real (auth on Grafana and `/metrics`, secrets, managed Redis, GPU for the model)?

## Interview angle

"Walk me through a project you shipped." Aegis v1.0: one `docker compose` command, a live dashboard, and measured numbers. The load test earned its keep. It exposed a cache stampede (11 identical requests → 11 model calls, p95 12.4 s), fixed with request coalescing (→ 1 call, p95 3.1 s). It also exposed two monitoring bugs: histogram buckets that capped at 10 s, and counters that hid their first burst. That's the story of measuring before claiming.
