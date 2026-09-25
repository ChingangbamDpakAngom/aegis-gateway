"""Load-test a running gateway and print a Markdown results row.

    uv run python scripts/load_test.py <scenario> --key aeg_... [--concurrency 20] [--requests 500]

Scenarios:
  cached     the same question every time -> measures the gateway itself (auth, rate
             limit, validation, cache) with the model out of the picture
  ratelimit  fire requests as fast as possible at one key -> shows 200s turning into 429s
  model      a different question every time -> every request reaches the LLM
"""

import argparse
import asyncio
import statistics
import time
from collections import Counter

import httpx2

TOPICS = ["Redis", "Docker", "HTTP", "TCP", "DNS", "Python", "SQL", "Git", "Linux", "JSON",
          "YAML", "REST", "OAuth", "TLS", "UTF-8", "Kafka", "NumPy", "pandas", "PyTorch", "Kubernetes"]


RUN = int(time.time())  # makes "model" questions new on every run, so none are cached


def message(scenario: str, i: int) -> str:
    if scenario == "model":
        return f"In one short sentence, what is {TOPICS[i % len(TOPICS)]}? (run {RUN}, #{i})"
    return "In one short sentence, what is Redis?"


async def run(url: str, key: str, scenario: str, concurrency: int, total: int, timeout: float):
    latencies, statuses = [], Counter()
    gate = asyncio.Semaphore(concurrency)  # at most `concurrency` requests in flight

    async with httpx2.AsyncClient(base_url=url, timeout=timeout, headers={"X-API-Key": key}) as http:
        if scenario == "cached":
            await http.post("/chat", json={"message": message(scenario, 0)})  # fill the cache first

        async def one(i: int):
            async with gate:
                start = time.perf_counter()
                try:
                    response = await http.post("/chat", json={"message": message(scenario, i)})
                    statuses[response.status_code] += 1
                except httpx2.HTTPError as exc:
                    statuses[type(exc).__name__] += 1
                latencies.append(time.perf_counter() - start)

        wall = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(total)))
        wall = time.perf_counter() - wall

    q = statistics.quantiles(latencies, n=100)
    status_text = ", ".join(f"{s}: {n}" for s, n in sorted(statuses.items(), key=str))
    print("| Scenario | Requests | Concurrency | Throughput | p50 | p95 | p99 | Status codes |")
    print("|---|---|---|---|---|---|---|---|")
    print(f"| {scenario} | {total} | {concurrency} | {total / wall:.1f} req/s | {q[49] * 1000:.0f} ms "
          f"| {q[94] * 1000:.0f} ms | {q[98] * 1000:.0f} ms | {status_text} |")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["cached", "ratelimit", "model"])
    parser.add_argument("--key", required=True)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.key, args.scenario, args.concurrency, args.requests, args.timeout))
