FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

COPY --from=ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock LICENSE ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev

# State lives in /var/data; mount a volume there to keep it across container replacements.
RUN useradd --system --no-create-home slothbot \
    && mkdir -p /var/data \
    && chown slothbot /var/data

ENV SB_DATA=/var/data \
    PATH="/app/.venv/bin:$PATH"

VOLUME /var/data
USER slothbot

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "slothbot.healthcheck"]

CMD ["slothbot"]
