# syntax=docker/dockerfile:1
# ---- Build stage ----
FROM golang:1.24-bookworm AS builder

WORKDIR /src

# Install templ. Using a pinned recent version; bump as needed.
RUN go install github.com/a-h/templ/cmd/templ@v0.3.819

COPY go.mod ./
# go.sum may be empty/missing on first build; that's fine — tidy will populate.
COPY go.sum* ./
RUN go mod download || true

COPY . .

# Generate .templ -> _templ.go, then build.
RUN templ generate
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/clock-go .

# ---- Litestream stage (small) ----
FROM litestream/litestream:0.3.13 AS litestream

# ---- Runtime ----
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates tzdata && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /out/clock-go /app/clock-go
COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/litestream

COPY litestream.yml /app/litestream.yml
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV DB_PATH=/app/data/clock-go.db
ENV PORT=8000

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
