"""Measure the prompt-injection guard on a labelled public dataset.

    uv run --group ml python scripts/eval_guard.py [model_name]

Dataset: deepset/prompt-injections, test split (label 1 = injection). Prints a
Markdown table of precision / recall / false-positive rate per threshold, and latency.
"""

import statistics
import sys
import time

import httpx2

from gateway import config
from gateway.core import guard

ROWS_URL = "https://datasets-server.huggingface.co/rows"


def fetch(dataset="deepset/prompt-injections", split="test"):
    rows, offset = [], 0
    while True:  # the rows API returns at most 100 rows per call
        page = httpx2.get(ROWS_URL, params={
            "dataset": dataset, "config": "default", "split": split, "offset": offset, "length": 100,
        }, timeout=30).json()["rows"]
        rows += [(r["row"]["text"], r["row"]["label"]) for r in page]
        if len(page) < 100:
            return rows
        offset += 100


def main(model_name: str):
    rows = fetch()
    score = guard.load(model_name)
    score("warm-up")  # the first call pays one-off setup costs; don't count it

    scored, latencies = [], []
    for text, label in rows:
        start = time.perf_counter()
        scored.append((score(text), label))
        latencies.append((time.perf_counter() - start) * 1000)

    positives = sum(label for _, label in scored)
    print(f"Model: `{model_name}`, {len(rows)} examples ({positives} injections)\n")
    print("| Threshold | Precision | Recall | F1 | False-positive rate |")
    print("|---|---|---|---|---|")
    for threshold in (0.5, 0.9, 0.99):
        tp = sum(s >= threshold and y == 1 for s, y in scored)
        fp = sum(s >= threshold and y == 0 for s, y in scored)
        fn = positives - tp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        fpr = fp / (len(rows) - positives)
        print(f"| {threshold} | {precision:.2f} | {recall:.2f} | {f1:.2f} | {fpr:.2f} |")

    q = statistics.quantiles(latencies, n=100)
    print(f"\nLatency per message (CPU): p50 {q[49]:.0f} ms, p95 {q[94]:.0f} ms")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else config.GUARD_MODEL)
