FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import redis; redis.from_url('${REDIS_URL}').ping()" || exit 1

# Run application
CMD ["sh", "-c", "gunicorn --workers 4 --worker-class gevent --bind 0.0.0.0:${PORT:-8000} app.server.app:app"]
