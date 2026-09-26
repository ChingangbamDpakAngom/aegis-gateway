FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first: this layer is reused until uv.lock changes, so code edits rebuild in seconds.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev

# The prompt guard needs PyTorch. The lock file's Linux torch pulls ~3 GB of CUDA libraries,
# so the optional guard build installs the CPU-only wheel instead.
# Trade-off: these two packages aren't pinned by uv.lock; pin them here if builds must be reproducible.
ARG WITH_GUARD=false
RUN if [ "$WITH_GUARD" = "true" ]; then \
      uv pip install torch --index-url https://download.pytorch.org/whl/cpu && \
      uv pip install "transformers>=5.17"; \
    fi

# Don't run as root: a bug in the app shouldn't hand out root inside the container.
# The cache dir must exist (owned by aegis) before a volume is mounted on it, or Docker
# creates it as root and the guard model can't be downloaded.
RUN useradd --create-home aegis && mkdir -p /home/aegis/.cache/huggingface && chown -R aegis /home/aegis/.cache
USER aegis
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "gateway.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
