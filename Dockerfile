FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl wget && rm -rf /var/lib/apt/lists/*
RUN pip install uv

# Install Litestream
# Install Litestream (auto-detect arch)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ]; then LS_ARCH="arm64"; else LS_ARCH="amd64"; fi && \
    wget -qO- https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-${LS_ARCH}.tar.gz | tar xz -C /usr/local/bin

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY . .
RUN chmod +x /app/entrypoint.sh

VOLUME /app/data

ENTRYPOINT ["/app/entrypoint.sh"]
