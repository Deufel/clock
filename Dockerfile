FROM python:3.14-slim

WORKDIR /app

# System deps + uv (single layer)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl wget && \
    rm -rf /var/lib/apt/lists/* && \
    curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH="/root/.local/bin:$PATH"

# Litestream (auto-detect arch)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ]; then LS_ARCH="arm64"; else LS_ARCH="amd64"; fi && \
    wget -qO- https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-${LS_ARCH}.tar.gz | tar xz -C /usr/local/bin

# Dependencies (cached unless lock changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# App code
COPY . .
RUN chmod +x /app/entrypoint.sh

VOLUME /app/data
HEALTHCHECK --interval=30s --timeout=3s CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
