# Deploying the live demo

The public demo runs on free tiers:

| Piece | Local | Deployed |
|---|---|---|
| Gateway (+ prompt guard) | Docker on your laptop | **Hugging Face Space** (Docker, free CPU, 16 GB RAM) |
| Redis | Docker | **Upstash** (managed Redis, free tier) |
| Model | Ollama on your GPU | **Groq** (OpenAI-compatible API, free tier) |
| Prometheus + Grafana | Docker | stay local; `/metrics` is switched off in public |

The gateway talks to any OpenAI-compatible server, so the only difference between local and deployed is configuration (`LLM_BASE_URL`, `LLM_API_KEY`, `MODEL`, `LARGE_MODEL`). Every push to `master` redeploys automatically via `.github/workflows/deploy-space.yml`.

**Secrets rule:** API keys and the Redis URL go only into the Space's *Secrets* and GitHub's *Secrets*, never into code, `.env` files in git, chat messages or screenshots.

## 1. Redis on Upstash

1. Sign up at <https://upstash.com> → **Create Database** → type *Redis*, name `aegis`, region **US East** (close to Hugging Face's servers), free plan.
2. On the database page, copy the **Redis URL** that starts with `rediss://` (two s's = TLS). It contains the password: treat it as a secret.

## 2. Model API on Groq

1. Sign up at <https://console.groq.com> → **API Keys** → **Create API Key**, name `aegis-space`.
2. Copy the key (starts with `gsk_`). It's shown once.

## 3. Hugging Face Space

1. <https://huggingface.co/new-space> → Owner: your account, Space name `aegis-gateway`, License MIT, SDK **Docker** → *Blank*, hardware **CPU basic (free)**, **Public** → *Create Space*. Leave it empty.
2. Space → **Settings → Variables and secrets**:

   **Secrets** (hidden):

   | Name | Value |
   |---|---|
   | `REDIS_URL` | your Upstash `rediss://…` URL |
   | `LLM_API_KEY` | your Groq `gsk_…` key |

   **Variables** (visible, not secret):

   | Name | Value | Why |
   |---|---|---|
   | `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | Groq instead of Ollama |
   | `MODEL` | `llama-3.1-8b-instant` | small, fast default |
   | `LARGE_MODEL` | `llama-3.3-70b-versatile` | for long / reasoning questions |
   | `METRICS_ENABLED` | `false` | don't publish `/metrics` |
   | `REDIS_TIMEOUT_S` | `2` | Redis is across the internet now, not on localhost |
   | `MODEL_TIMEOUT_S` | `30` | hosted models are fast; fail sooner |
   | `LARGE_MODEL_TIMEOUT_S` | `60` | |

3. Create a Hugging Face **access token**: <https://huggingface.co/settings/tokens> → *Create new token* → type **Write**, or a fine-grained token with write access to this Space only. Copy it (starts with `hf_`).

## 4. Connect GitHub to the Space

In the GitHub repo → **Settings → Secrets and variables → Actions**:

- **Secrets** tab → *New repository secret*: `HF_TOKEN` = the `hf_…` token.
- **Variables** tab → *New repository variable*: `HF_SPACE` = `<your-hf-username>/aegis-gateway`.

Then **Actions → Deploy to Hugging Face Space → Run workflow** (or just push to `master`). The workflow copies the repo, turns the prompt guard on, adds the Space's settings header and pushes it to the Space. The first build takes ~10 minutes (PyTorch is large). Follow it in the Space's **Logs** tab.

## 5. Create a demo API key

Keys live in Redis, so create one against Upstash from your laptop. In PowerShell, from the `gateway` folder:

```powershell
$env:REDIS_URL = "<paste your Upstash rediss:// URL>"
uv run python -m gateway.keys demo --capacity 5 --refill-per-s 0.05
Remove-Item Env:REDIS_URL
```

This prints an `aeg_…` key with a small shared allowance (5 requests, then one every 20 s) that protects your free Groq quota. It's fine to publish **this** key in the README for recruiters to try. If it's abused, delete it and make a new one.

## 6. Check it

- `https://<your-hf-username>-aegis-gateway.hf.space/health` → `{"status":"OK"}`
- `https://<your-hf-username>-aegis-gateway.hf.space/ready` → `{"status":"ready"}` (Redis reachable)
- Open the Space URL → it redirects to `/docs` → **Authorize** with the demo key → try `POST /chat`.
- `/metrics` → 404 (switched off).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `/ready` → 503 | `REDIS_URL` wrong, or it starts with `redis://` instead of `rediss://` |
| `/chat` → 502 `model_unavailable` | `LLM_API_KEY` wrong, or a model name changed. Check Groq's Models page. |
| Space stuck on "Building" / crash in Logs | Read the Logs tab. A first build of the guard image can take 10+ minutes. |
| First request after a while takes ~30 s | Free Spaces sleep when idle; the first visit wakes them |
| 429 for everyone | The shared demo key is out of tokens; wait, or raise `--refill-per-s` |
