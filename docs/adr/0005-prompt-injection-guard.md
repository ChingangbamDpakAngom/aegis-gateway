# ADR-005: Screen prompts with a small classifier before the LLM, and measure it

**Status:** Accepted · **Date:** 2026-09-25

## Context

Prompt injection means text that tries to override the model's instructions ("ignore previous instructions and…"). It's the top risk in the [OWASP Top 10 for LLM applications](https://genai.owasp.org/llmrisk/llm01-prompt-injection/). A gateway sees every prompt, so it's a natural place for a cheap first check. The plan was Meta's Llama Prompt Guard 2 (86M), but it's gated on Hugging Face and access for this account wasn't approved yet.

## Decision

- **A fine-tuned text classifier, not the LLM itself, not regex.** `protectai/deberta-v3-base-prompt-injection-v2` (184M, Apache 2.0, ungated) outputs SAFE/INJECTION probabilities in about 180 ms on a laptop CPU. Regex misses rephrasings. Asking the LLM "is this an attack?" costs a full generation and can itself be injected.
- **Swappable by config.** `GUARD_MODEL` accepts any Hugging Face text classifier whose malicious label is `INJECTION`, `JAILBREAK`, `MALICIOUS` or `LABEL_1`. Prompt Guard 2 works by changing one environment variable once access is granted.
- **Position: after rate limiting and validation, before the model.** The classifier costs CPU, so abusive clients are stopped by the cheap rate limit first. The LLM is the expensive part, so anything flagged never reaches it. A test proves a blocked prompt makes no model call.
- **Refuse with `400 prompt_rejected`** at `GUARD_THRESHOLD` (default 0.5). The score is logged as `guard_score` on every request, so thresholds can be tuned from real traffic.
- **Chunk long messages.** The classifier reads 512 tokens, so a message is split into 500-token chunks and scored by its worst chunk. With plain truncation, an attack placed after 512 tokens of filler would never be seen.
- **Run it in a worker thread** (`asyncio.to_thread`), because inference is CPU-bound and would otherwise freeze the event loop for every other request.
- **Loaded once at startup**, lazily importing `transformers`, so PyTorch stays in the optional `ml` dependency group and CI doesn't install it (tests use a fake scorer).

## Evaluation

`scripts/eval_guard.py` on the `deepset/prompt-injections` test split (116 prompts, 60 injections):

| Threshold | Precision | Recall | F1 | False-positive rate |
|---|---|---|---|---|
| 0.5 | 1.00 | 0.37 | 0.54 | 0.00 |
| 0.9 | 1.00 | 0.33 | 0.50 | 0.00 |
| 0.99 | 1.00 | 0.30 | 0.46 | 0.00 |

Latency per message (CPU): p50 181 ms, p95 323 ms.

**Reading it:** everything it blocked really was an injection, but it missed about two thirds. The misses are largely explained by the dataset. 22 of the 60 injections are German, and this model is trained on English. The dataset also labels plain role-play requests ("I want you to act as an interviewer…") as injections, which the model reasonably treats as safe. 0.5 is kept as the threshold: the highest recall, with no false positives on this set.

## Consequences

- **This is a tripwire, not a defence.** It stops the obvious attacks cheaply. The real protections are downstream: never give the LLM secrets or tools it doesn't need, and treat its output as untrusted.
- **Known false positives on long, repetitive text.** In manual tests, a harmless paragraph repeated six times, or template-generated sentences, scored ~0.92–0.99 "injection". The benchmark doesn't contain such inputs, so its 0% false-positive rate is optimistic. Watch `guard_score` and the 400 rate in production before raising stakes.
- Adds ~0.2 s CPU per request and ~700 MB of RAM for PyTorch plus the model. At higher traffic, batch requests or run the classifier as its own service on a GPU.
- A crash inside the classifier surfaces as a 500 and the prompt doesn't reach the model, so it fails closed.
- Next: evaluate Prompt Guard 2 with the same script once access is approved (it's multilingual, which should lift recall on the German half), and add a multilingual or in-domain test set.
