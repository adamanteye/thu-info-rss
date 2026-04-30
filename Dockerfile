# Single-stage Alpine build
FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  UV_COMPILE_BYTECODE=1 \
  UV_LINK_MODE=copy \
  DB_PATH=/data/articles.db \
  PATH="/app/.venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Install uv in Alpine
RUN python -m pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY . .

# Create data directory for SQLite database and persistent storage
RUN mkdir -p /data

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5).read()"

# Run the application
CMD ["uvicorn", "app:app", "--host", "::", "--port", "8000"]
