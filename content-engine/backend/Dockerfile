# ─────────────────────────────────────────────────────────────────────────────
# Python Backend Dockerfile
# Single image used for BOTH the FastAPI server and the Celery worker.
# Stage 1: builder — compiles/installs deps
# Stage 2: runtime — lean final image
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# System deps needed for some Python packages (psycopg2-binary compiles from src)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy and install Python dependencies into an isolated prefix
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Minimal runtime system deps + Playwright dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    libnss3 \
    libnspr4 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxtst6 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Install Playwright browsers (chromium)
RUN playwright install chromium --with-deps

# Create non-root user for security
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

# Copy application source (in dev this is overridden by the volume mount)
COPY --chown=appuser:appgroup . .

# Switch to non-root user
USER appuser

# Expose the FastAPI port (Celery worker doesn't use this, but harmless)
EXPOSE 8000

# Default command is overridden in docker-compose.yml per-service
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
