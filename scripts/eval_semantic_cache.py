"""Choose the semantic-cache similarity threshold from labelled question pairs.

    uv run python scripts/eval_semantic_cache.py [embed_model]

"Same" pairs ask the same thing in different words: a cache hit is correct.
"Different" pairs share a topic but need a different answer: a hit would return a
WRONG answer. For a cache, a wrong hit is far worse than a miss, so pick the lowest
threshold with zero wrong hits.
"""

import math
import sys

import httpx2

from gateway import config

SAME = [
    ("What is Redis?", "Can you explain what Redis is?"),
    ("How do I reverse a list in Python?", "What's the way to reverse a Python list?"),
    ("What is the capital of France?", "Which city is the capital of France?"),
    ("Explain the token bucket algorithm", "How does the token bucket algorithm work?"),
    ("How many days are in a leap year?", "A leap year has how many days?"),
    ("What does HTTP 429 mean?", "What is the meaning of HTTP status 429?"),
    ("Give me a recipe for pancakes", "How do I make pancakes?"),
    ("What is machine learning?", "Explain machine learning"),
    ("How do I install Docker on Windows?", "Steps to install Docker on Windows"),
    ("What is the boiling point of water?", "At what temperature does water boil?"),
    ("Translate 'good morning' into French", "How do you say good morning in French?"),
    ("Who wrote Hamlet?", "Hamlet was written by whom?"),
]
DIFFERENT = [
    ("What is the capital of France?", "What is the capital of Germany?"),
    ("How do I reverse a list in Python?", "How do I sort a list in Python?"),
    ("What does HTTP 429 mean?", "What does HTTP 404 mean?"),
    ("How do I install Docker on Windows?", "How do I install Docker on Linux?"),
    ("What is the boiling point of water?", "What is the freezing point of water?"),
    ("Translate 'good morning' into French", "Translate 'good morning' into Spanish"),
    ("Who wrote Hamlet?", "Who wrote Macbeth?"),
    ("How many days are in a leap year?", "How many days are in February?"),
    ("Give me a recipe for pancakes", "Give me a recipe for waffles"),
    ("What is Redis?", "What is PostgreSQL?"),
    ("Convert 10 miles to kilometres", "Convert 10 kilometres to miles"),
    ("Is 17 a prime number?", "Is 21 a prime number?"),
]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


def similarities(model, pairs):
    texts = [t for pair in pairs for t in pair]
    data = httpx2.post(f"{config.LLM_BASE_URL}/embeddings", json={"model": model, "input": texts},
                       timeout=120).json()["data"]
    vectors = [item["embedding"] for item in data]
    return [cosine(vectors[i], vectors[i + 1]) for i in range(0, len(vectors), 2)]


def main(model):
    same, different = similarities(model, SAME), similarities(model, DIFFERENT)
    print(f"Embedding model: `{model}`, {len(SAME)} same-meaning and {len(DIFFERENT)} different-meaning pairs\n")
    print("| Threshold | Correct hits (same) | Wrong hits (different) |")
    print("|---|---|---|")
    for threshold in (0.80, 0.85, 0.90, 0.95, 0.98):
        hits = sum(s >= threshold for s in same)
        wrong = sum(s >= threshold for s in different)
        print(f"| {threshold} | {hits}/{len(same)} | {wrong}/{len(different)} |")
    worst = max(zip(different, DIFFERENT))
    print(f"\nMost similar DIFFERENT pair ({worst[0]:.3f}): {worst[1]}")
    print(f"Least similar SAME pair ({min(same):.3f}): {SAME[same.index(min(same))]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all-minilm")
