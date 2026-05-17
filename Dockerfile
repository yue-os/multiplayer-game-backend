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

# Health check the Flask app itself. Redis is optional because OTP registration
# falls back to Postgres when Redis is unavailable.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8000\")}/health', timeout=5).read()" || exit 1

# Run application
CMD ["sh", "-c", "gunicorn --workers 4 --worker-class gevent --bind 0.0.0.0:${PORT:-8000} app.server.app:app"]
