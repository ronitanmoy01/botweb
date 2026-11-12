# Python 3.12 slim base
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOVIEHUB_ENVIRONMENT=production

WORKDIR /app

# Install uv for fast, locked dependency installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Install runtime dependencies from uv.lock (reproducible, no dev group)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Persist uploaded media outside the image layer
VOLUME /app/uploads

# Apply migrations then launch the app
EXPOSE 8000
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
