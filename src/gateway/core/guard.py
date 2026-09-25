"""Prompt-injection screening with a small text classifier, run before the LLM."""

import time
from collections.abc import Callable

from prometheus_client import Histogram

LATENCY = Histogram("aegis_guard_duration_seconds", "Time to screen one message")

# Label names differ per model: Prompt Guard 2 uses LABEL_1, ProtectAI uses INJECTION.
MALICIOUS_LABELS = {"INJECTION", "JAILBREAK", "MALICIOUS", "LABEL_1"}
# The classifiers read at most 512 tokens; longer messages are split so an attack
# can't hide past the cut-off. 500 leaves room for the special tokens.
CHUNK_TOKENS = 500


def load(model_name: str) -> Callable[[str], float]:
    """Load the classifier once; return a function text -> injection probability (0-1)."""
    from transformers import pipeline  # imported here: torch is only needed when the guard is on

    return scorer(pipeline("text-classification", model=model_name))


def scorer(classifier) -> Callable[[str], float]:
    """Wrap a Hugging Face text-classification pipeline (or a fake with the same shape)."""
    tokenizer = classifier.tokenizer

    def injection_score(text: str) -> float:
        start = time.perf_counter()
        ids = tokenizer(text, add_special_tokens=False)["input_ids"]
        chunks = [tokenizer.decode(ids[i:i + CHUNK_TOKENS]) for i in range(0, len(ids), CHUNK_TOKENS)]
        results = classifier(chunks or [text], top_k=None, truncation=True, max_length=512)
        # The message is as dangerous as its most suspicious chunk.
        score = max(s["score"] for chunk in results for s in chunk if s["label"] in MALICIOUS_LABELS)
        LATENCY.observe(time.perf_counter() - start)
        return score

    return injection_score
