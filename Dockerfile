FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY . .

CMD ["uv", "run", "stario", "serve", "main:bootstrap", "--host", "0.0.0.0"]
