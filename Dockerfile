# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- Stage 2: Production ----
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Security: run as non-root user
RUN useradd -m appuser

# Copy application code
COPY . .

# Create data directory with correct ownership
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

# Shell form (not exec-form JSON array) so $PORT is expanded at container start - platforms like
# Render inject a PORT env var and require the app to bind to it; a hardcoded exec-form CMD would
# ignore it. ${PORT:-8000} falls back to 8000 when PORT isn't set (e.g. local docker-compose).
CMD uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}
