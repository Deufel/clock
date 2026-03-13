FROM python:3.14-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy dependency files first (for caching)
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --no-dev --frozen

# Copy app code
COPY . .

# Run the app
CMD ["uv", "run", "main.py"]
